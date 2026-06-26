from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction

from crawler.models import Paper, Author, Category
from crawler.tasks import (
    fetch_arxiv_page, parse_arxiv_atom, ARXIV_BATCH_SIZE,
    arxiv_doi, build_arxiv_bibtex, _build_arxiv_query, download_pdf,
)
from crawler.parsers import extract_keywords_with_llm
from crawler.services.embed_client import embed_paper
from crawler.services.venue_client import map_paper_venue


def _keywords_to_list(raw: str) -> list:
    """Normalize the LLM's comma-separated keyword string into a JSON list."""
    if not raw:
        return []
    return [k.strip() for k in raw.split(',') if k.strip()]


class Command(BaseCommand):
    help = 'Crawl ArXiv cs.* papers for a past date window. Useful for testing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-papers',
            type=int,
            default=10,
            help='Maximum number of papers to crawl (default: 10; 0 = no limit)',
        )
        parser.add_argument(
            '--lookback-start',
            type=int,
            default=getattr(settings, 'ARXIV_LOOKBACK_START_DAYS', 5),
            help='Oldest day of the window (days back from today). Default: settings.ARXIV_LOOKBACK_START_DAYS',
        )
        parser.add_argument(
            '--lookback-end',
            type=int,
            default=getattr(settings, 'ARXIV_LOOKBACK_END_DAYS', 3),
            help='Newest day of the window (days back from today). Default: settings.ARXIV_LOOKBACK_END_DAYS',
        )
        parser.add_argument(
            '--no-llm',
            action='store_true',
            help='Skip LLM keyword extraction (faster for testing)',
        )
        parser.add_argument(
            '--download',
            action='store_true',
            help='Download PDFs synchronously inline (testing without a Celery worker)',
        )

    def handle(self, *args, **options):
        max_papers = options['max_papers']
        skip_llm = options['no_llm']
        do_download = options['download']
        today = timezone.now().date()
        date_from = today - timedelta(days=options['lookback_start'])
        date_to = today - timedelta(days=options['lookback_end'])
        search_query = _build_arxiv_query(date_from, date_to)
        per_page = int(getattr(settings, 'ARXIV_MAX_RESULTS', ARXIV_BATCH_SIZE))

        limit = max_papers if max_papers and max_papers > 0 else None
        self.stdout.write(
            f'Crawling ArXiv papers submitted {date_from} .. {date_to} '
            f'(limit: {limit or "no limit"})...'
        )
        if skip_llm:
            self.stdout.write(self.style.WARNING('LLM keyword extraction disabled.'))
        if do_download:
            self.stdout.write(self.style.WARNING('PDF download enabled (synchronous).'))

        start = 0
        total_created = 0

        while limit is None or total_created < limit:
            self.stdout.write(f'  Fetching window page start={start}, size={per_page}...')
            xml_content = fetch_arxiv_page(start, per_page, search_query=search_query)
            if not xml_content:
                self.stderr.write(self.style.ERROR('Failed to fetch ArXiv page. Stopping.'))
                break

            papers = parse_arxiv_atom(xml_content)
            if not papers:
                self.stdout.write('No papers returned. Reached end of window.')
                break

            # Dedup against the shared `papers` table only (there is no crawl_tasks table).
            batch_urls = [p['url'] for p in papers if 'url' in p]
            existing_urls = set(
                Paper.objects.filter(url__in=batch_urls).values_list('url', flat=True)
            )

            for paper_data in papers:
                if limit is not None and total_created >= limit:
                    break

                url = paper_data.get('url', '')
                if not url or url in existing_urls:
                    self.stdout.write(f'  Skip (duplicate): {url}')
                    continue

                try:
                    with transaction.atomic():
                        keywords = [] if skip_llm else _keywords_to_list(
                            extract_keywords_with_llm(paper_data.get('abstract', ''))
                        )

                        author_objects = []
                        for name in paper_data.get('authors', []):
                            author, _ = Author.objects.get_or_create(
                                name=name,
                                defaults={
                                    'email': '', 'affiliation': '',
                                    'bio': '', 'google_scholar_url': '',
                                },
                            )
                            author_objects.append(author)

                        category_objects = []
                        for cat_name in paper_data.get('categories', []):
                            category, _ = Category.objects.get_or_create(
                                name=cat_name,
                                defaults={'description': ''},
                            )
                            category_objects.append(category)

                        # doi: prefer publisher DOI from the feed, else the
                        # arXiv DataCite DOI. bibtex: generated arXiv entry.
                        # method/results/conclusions left '' for the downstream
                        # enrichment pipeline. conference_id/journal_id -> NULL.
                        doi = paper_data.get('doi') or arxiv_doi(paper_data.get('arxiv_id'))
                        paper = Paper.objects.create(
                            title=paper_data.get('title', '[Title Not Found]'),
                            abstract=paper_data.get('abstract', ''),
                            doi=doi,
                            publication_date=paper_data.get('publication_date') or None,
                            url=url,
                            pdf_url=paper_data.get('pdf_url', ''),
                            keywords=keywords,
                            bibtex=build_arxiv_bibtex(paper_data),
                            file_format='pdf',
                        )

                        if author_objects:
                            paper.authors.set(author_objects)
                        if category_objects:
                            paper.categories.set(category_objects)

                        total_created += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'  [{total_created}] {paper_data.get("title", "")[:70]}'
                            )
                        )
                        
                    # Download disabled — use pdf_url (ArXiv); local file only via user upload.
                    # if do_download and paper.pdf_url:
                    #     download_pdf(paper.pdf_url, paper)
                    # Venue mapping + embed (same as Celery ArXiv task; outside transaction).
                    if map_paper_venue(paper.id):
                        self.stdout.write('    venue mapping: ok')
                    else:
                        self.stdout.write(self.style.WARNING('    venue mapping: skipped/failed'))
                    if embed_paper(paper):
                        self.stdout.write('    embed: ok')
                    else:
                        self.stdout.write(self.style.WARNING('    embed: skipped/failed'))        
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f'  Error saving {url}: {e}'))

            if len(papers) < per_page:
                break

            start += per_page

        self.stdout.write(self.style.SUCCESS(f'Done. Created {total_created} papers.'))
