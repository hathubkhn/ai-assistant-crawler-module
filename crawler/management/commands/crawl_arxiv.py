from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction

from crawler.models import Paper, Author, Category
from crawler.tasks import (
    fetch_arxiv_page, parse_arxiv_atom, ARXIV_BATCH_SIZE,
    arxiv_doi, build_arxiv_bibtex,
)
from crawler.parsers import extract_keywords_with_llm


def _keywords_to_list(raw: str) -> list:
    """Normalize the LLM's comma-separated keyword string into a JSON list."""
    if not raw:
        return []
    return [k.strip() for k in raw.split(',') if k.strip()]


class Command(BaseCommand):
    help = 'Crawl ArXiv cs.* papers immediately. Useful for testing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-papers',
            type=int,
            default=10,
            help='Maximum number of papers to crawl (default: 10)',
        )
        parser.add_argument(
            '--no-llm',
            action='store_true',
            help='Skip LLM keyword extraction (faster for testing)',
        )
        parser.add_argument(
            '--ignore-date',
            action='store_true',
            help="Crawl the newest papers regardless of publish date "
                 "(useful for testing on weekends when ArXiv has no same-day papers)",
        )

    def handle(self, *args, **options):
        max_papers = options['max_papers']
        skip_llm = options['no_llm']
        ignore_date = options['ignore_date']
        today = timezone.now().date()

        self.stdout.write(f'Crawling up to {max_papers} ArXiv papers (date: {today})...')
        if skip_llm:
            self.stdout.write(self.style.WARNING('LLM keyword extraction disabled.'))
        if ignore_date:
            self.stdout.write(self.style.WARNING('Date filter disabled (crawling newest regardless of date).'))

        start = 0
        total_created = 0

        while total_created < max_papers:
            remaining = max_papers - total_created
            batch_size = min(remaining, ARXIV_BATCH_SIZE)

            self.stdout.write(f'  Fetching batch start={start}, size={batch_size}...')
            xml_content = fetch_arxiv_page(start, batch_size)
            if not xml_content:
                self.stderr.write(self.style.ERROR('Failed to fetch ArXiv page. Stopping.'))
                break

            papers = parse_arxiv_atom(xml_content)
            if not papers:
                self.stdout.write('No papers returned. Stopping.')
                break

            # Dedup against the shared `papers` table only (there is no crawl_tasks table).
            batch_urls = [p['url'] for p in papers if 'url' in p]
            existing_urls = set(
                Paper.objects.filter(url__in=batch_urls).values_list('url', flat=True)
            )

            found_old = False
            for paper_data in papers:
                if total_created >= max_papers:
                    break

                pub_dt = paper_data.get('published_dt')
                if not ignore_date and pub_dt and pub_dt.date() < today:
                    found_old = True
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
                                f'  [{total_created}/{max_papers}] {paper_data.get("title", "")[:70]}'
                            )
                        )

                except Exception as e:
                    self.stderr.write(self.style.ERROR(f'  Error saving {url}: {e}'))

            if found_old or len(papers) < batch_size:
                break

            start += batch_size

        self.stdout.write(self.style.SUCCESS(f'Done. Created {total_created} papers.'))
