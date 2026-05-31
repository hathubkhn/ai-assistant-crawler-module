import requests
import logging
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from django.core.files.base import ContentFile
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
import tqdm
import time
import re

from .models import CrawlTask, Paper, Author, Category, Dataset
from .parsers import fetch_sitemap_content, parse_paper_page_html, parse_dataset_page, extract_keywords_with_llm
from .services.embed_client import embed_paper
from .services.venue_client import map_paper_venue

# Configure logging
logger = logging.getLogger(__name__)

# URL for the Papers With Code sitemap
PWC_SITEMAP_URL = "https://paperswithcode.com/sitemap.xml"
PWC_DOMAIN = "paperswithcode.com"
PWC_PAPER_PATH_PREFIX = "/paper/"
PWC_BASE_URL = f'https://{PWC_DOMAIN}'

def fetch_sitemap(url: str) -> str | None:
    """Fetches content from the given URL. Returns text content or None on error."""
    try:
        response = requests.get(url, timeout=300) # Increased timeout for potentially large sitemaps
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        logger.info(f"Successfully fetched sitemap from {url}")
        return response.text
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching sitemap from {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred fetching sitemap {url}: {e}")
        return None

# --- PWC Sitemap Check Task --- #
@shared_task(name='crawler.tasks.check_pwc_sitemap')
def check_pwc_sitemap_and_create_tasks():
    """Celery task to fetch PWC sitemap, parse new papers, and create crawl tasks."""
    logger.info("Starting PWC sitemap check task...")
    
    sitemap_content = fetch_sitemap(PWC_SITEMAP_URL)
    if not sitemap_content:
        logger.warning("Failed to fetch PWC sitemap content. Task exiting.")
        return

    logger.info("Parsing PWC sitemap content...")
    try:
        # Parse the main sitemap index
        root = ET.fromstring(sitemap_content)
        
        # Find all sub-sitemap URLs for papers
        paper_sitemaps = []
        for loc_element in root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc'):
            if loc_element.text and 'sitemap-papers.xml' in loc_element.text:
                paper_sitemaps.append(loc_element.text)
        
        # Fetch and parse each paper sub-sitemap
        for sub_sitemap_url in tqdm.tqdm(paper_sitemaps):
            sub_content = fetch_sitemap_content(sub_sitemap_url)
            parsed_urls, new_parsed_urls = [], []
            if not sub_content:
                continue
                
            try:
                sub_root = ET.fromstring(sub_content)
                for loc_element in sub_root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc'):
                    if loc_element.text:
                        url = loc_element.text.strip()
                        parsed_url = urlparse(url)
                        if (parsed_url.netloc == PWC_DOMAIN and 
                            parsed_url.path.startswith(PWC_PAPER_PATH_PREFIX)):
                            parsed_urls.append(url)

                    if len(parsed_urls)==500:
                        existing_urls = set(
                            CrawlTask.objects.filter(url__in=parsed_urls)
                                        .values_list('url', flat=True)
                        )
                        new_parsed_urls = list(set(parsed_urls) - existing_urls)
                        parsed_urls = []
                
                for url in new_parsed_urls:
                    new_task = CrawlTask(url=url, status='pending')
                    new_task.save()
                    crawl_paper_details.delay(new_task.id)

            except ET.ParseError as e:
                logger.error(f"Error parsing sub-sitemap {sub_sitemap_url}: {e}")
                continue
                
    except ET.ParseError as e:
        logger.error(f"Error parsing main sitemap XML: {e}")
        return 0
    except Exception as e:
        logger.exception("An unexpected error occurred during sitemap parsing")
        return 0

    logger.info("PWC sitemap check task finished.")

# --- PDF Download Helper --- #
def download_pdf(pdf_url: str, paper_instance: Paper):
    """Downloads PDF from url and saves it to the paper's pdf_file field."""
    if not pdf_url:
        logger.warning(f"No PDF URL provided for Paper ID {paper_instance.id}. Skipping download.")
        return

    logger.info(f"Attempting to download PDF from {pdf_url} for Paper ID {paper_instance.id}")
    
    # Add a 15-second delay before any PDF download
    logger.info("Waiting 15 seconds before downloading...")
    time.sleep(15)
    
    # Headers to mimic a browser request
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; PaperCrawler/1.0; +https://yourwebsite.com/bot.html)',
        'Accept': 'application/pdf,application/x-pdf,*/*',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    try:
        # If it's an arXiv URL, modify it to use their export format
        if 'arxiv.org' in pdf_url:
            # Extract arXiv ID from the URL
            arxiv_id_match = re.search(r'(\d{4}\.\d{4,5}(?:v\d+)?)', pdf_url)
            if arxiv_id_match:
                arxiv_id = arxiv_id_match.group(1)
                # Use the export format URL which is more stable
                pdf_url = f'https://export.arxiv.org/pdf/{arxiv_id}'
                logger.info(f"Modified arXiv URL to: {pdf_url}")
            
            # Add a delay before requesting from arXiv to respect rate limits
            time.sleep(3)  # Wait 3 seconds between requests

        # First request to handle redirects and get the final URL
        session = requests.Session()
        
        # Try up to 3 times with increasing delays
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = session.get(pdf_url, headers=headers, timeout=180, allow_redirects=True)
                response.raise_for_status()
                
                # Check if we got a reCAPTCHA page
                if 'recaptcha' in response.text.lower():
                    wait_time = (attempt + 1) * 60  # Wait 1 minute, then 2, then 3
                    logger.warning(f"Encountered reCAPTCHA on attempt {attempt + 1}. Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
                
                # Get the content
                content = response.content

                # Verify we actually got a PDF
                if 'application/pdf' not in response.headers.get('content-type', '').lower():
                    if not content.startswith(b'%PDF-'):
                        logger.warning(f"URL {pdf_url} did not return a valid PDF on attempt {attempt + 1}. Retrying...")
                        time.sleep((attempt + 1) * 5)  # Increasing delay between attempts
                        continue
                
                # If we got here, we have a valid PDF
                break
                
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:  # Don't sleep on the last attempt
                    wait_time = (attempt + 1) * 5
                    logger.warning(f"Request failed on attempt {attempt + 1}. Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                else:
                    raise  # Re-raise the exception if we're out of retries
        else:
            logger.error(f"Failed to download PDF after {max_retries} attempts for Paper ID {paper_instance.id}")
            return

        # Create a filename using paper ID
        file_name = f"paper_{paper_instance.id}.pdf"
        
        # Save the PDF content
        paper_instance.pdf_file.save(file_name, ContentFile(content), save=True)
        logger.info(f"Successfully downloaded and saved PDF to {paper_instance.pdf_file.name} for Paper ID {paper_instance.id}")

    except requests.exceptions.RequestException as e:
        logger.error(f"HTTP error downloading PDF from {pdf_url} for Paper ID {paper_instance.id}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            if e.response.status_code == 429:
                logger.warning("Rate limit detected. Implementing mandatory cool-down period.")
                time.sleep(300)  # 5-minute cooldown
            elif e.response.status_code == 403:
                logger.warning("Access forbidden. Might need to review crawler behavior or implement longer cool-down.")
    except Exception as e:
        logger.exception(f"Unexpected error downloading/saving PDF from {pdf_url} for Paper ID {paper_instance.id}")


# --- Paper Crawling Task --- #
@shared_task(name='crawler.tasks.crawl_paper', bind=True, max_retries=3, default_retry_delay=60)
def crawl_paper_details(self, task_id: int):
    """Celery task to crawl details for a single paper given a CrawlTask ID."""
    logger.info(f"Starting paper crawl for CrawlTask ID: {task_id}")

    try:
        task = CrawlTask.objects.get(pk=task_id)
    except CrawlTask.DoesNotExist:
        logger.error(f"CrawlTask with ID {task_id} not found. Task cannot proceed.")
        return
    except Exception as e:
        logger.exception(f"Error retrieving CrawlTask ID {task_id}. Retrying...")
        raise self.retry(exc=e)
        
    if task.status != 'pending':
        logger.warning(f"CrawlTask ID {task_id} is not in pending state (state={task.status}). Skipping crawl.")
        return

    # 1. Fetch HTML Content
    html_content = None
    try:
        response = requests.get(task.url, timeout=300) 
        response.raise_for_status()
        html_content = response.text
        logger.info(f"Successfully fetched content from {task.url}")
    except requests.exceptions.RequestException as e:
        logger.error(f"HTTP error fetching {task.url} for Task ID {task_id}: {e}. Retrying...")
        raise self.retry(exc=e)
    except Exception as e:
        logger.exception(f"Unexpected error fetching {task.url} for Task ID {task_id}. Failing task.")
        task.status = 'failed'
        task.save(update_fields=['status', 'updated_at'])
        return

    # 2. Parse HTML Content
    parsed_data = None
    try:
        parsed_data = parse_paper_page_html(html_content, base_url=task.url)
        if not parsed_data:
            raise ValueError("Parser returned None or empty data.")
        logger.info(f"Successfully parsed content for Task ID {task_id}")
    except Exception as e:
        logger.exception(f"Error parsing HTML content for Task ID {task_id}. Failing task.")
        task.status = 'failed'
        task.save(update_fields=['status', 'updated_at'])
        return

    # 3. Create/Update Database Records (within a transaction)
    new_paper = None
    try:
        with transaction.atomic():
            # Create Authors (parser returns list of author names)
            author_objects = []
            for author_name in parsed_data.get('authors', []):
                if not author_name or not isinstance(author_name, str):
                    logger.warning(f"Skipping invalid author name: {author_name}")
                    continue
                try:
                    author, created = Author.objects.get_or_create(name=author_name.strip())
                    author_objects.append(author)
                    if created:
                        logger.info(f"Created new Author: {author_name}")
                except Exception as author_ex:
                    logger.error(f"Error getting/creating author '{author_name}': {author_ex}")
            
            # Create Categories
            category_objects = []
            for category_name in parsed_data.get('categories', []):
                if not category_name or not isinstance(category_name, str):
                    logger.warning(f"Skipping invalid category name: {category_name}")
                    continue
                try:
                    category = Category.objects.filter(name=category_name.strip()).first()
                    if not category:
                        category = Category.objects.create(name=category_name.strip())
                    category_objects.append(category)
                    logger.info(f"Created new Category: {category_name}")
                except Exception as cat_ex:
                    logger.error(f"Error getting/creating category '{category_name}': {cat_ex}")

            # Create Datasets (parser returns list of dicts with 'name' and 'url')
            dataset_objects = []
            for dataset_info in parsed_data.get('datasets', []):
                if not isinstance(dataset_info, dict) or 'name' not in dataset_info:
                    logger.warning(f"Skipping invalid dataset info: {dataset_info}")
                    continue
                try:
                    # Run dataset crawling task synchronously
                    dataset_id = crawl_dataset(
                        dataset_name=dataset_info['name'].strip(),
                        dataset_url=dataset_info.get('url', ''),
                    )
                    logger.info(f"Completed dataset crawling for {dataset_info['name']}")
                    
                    # Get the dataset object to link with paper
                    dataset = Dataset.objects.get(id=dataset_id)
                    dataset_objects.append(dataset)
                    
                except Exception as ds_ex:
                    logger.error(f"Error processing dataset '{dataset_info}': {ds_ex}")

            # Create the Paper record with all fields
            paper_data = {
                'title': parsed_data.get('title', '[Title Not Found]'),
                'abstract': parsed_data.get('abstract', '[Abstract Not Found]'),
                'doi': parsed_data.get('doi'),
                'publication_date': parsed_data.get('publication_date'),
                'journal_or_conference': parsed_data.get('journal_or_conference', ''),
                'file_format': parsed_data.get('file_format', 'pdf'),
                'keywords': list(parsed_data.get('keywords', []) or []),
                'url': task.url,
                'pdf_url': parsed_data.get('pdf_url'),
                'github_url': parsed_data.get('github_url'),
                'download_count': parsed_data.get('download_count', 0),
                'views_count': parsed_data.get('views_count', 0),
                'citations_count': parsed_data.get('citations_count', 0)
            }
            # Remove None values to use model defaults
            paper_data = {k: v for k, v in paper_data.items() if v is not None}

            new_paper = Paper.objects.create(**paper_data)
            logger.info(f"Created Paper record ID {new_paper.id} for Task ID {task_id}")

            # Set many-to-many relationships
            if author_objects:
                new_paper.authors.set(author_objects)
                logger.info(f"Linked {len(author_objects)} authors to Paper ID {new_paper.id}")
            if category_objects:
                new_paper.categories.set(category_objects)
                logger.info(f"Linked {len(category_objects)} categories to Paper ID {new_paper.id}")
            if dataset_objects:
                new_paper.datasets.set(dataset_objects)
                logger.info(f"Linked {len(dataset_objects)} datasets to Paper ID {new_paper.id}")

            # Add references if they exist
            if parsed_data.get('references'):
                existing_papers = Paper.objects.filter(doi__in=parsed_data['references'])
                if existing_papers.exists():
                    new_paper.references.set(existing_papers)
                    logger.info(f"Linked {existing_papers.count()} references to Paper ID {new_paper.id}")

            # Update the CrawlTask
            task.paper = new_paper
            task.status = 'completed'
            task.updated_at = timezone.now()
            task.save(update_fields=['paper', 'status', 'updated_at'])
            logger.info(f"Successfully completed DB operations for CrawlTask ID: {task_id}")

    except Exception as e:
        logger.exception(f"Database error during record creation/update for Task ID {task_id}. Retrying...")
        raise self.retry(exc=e)

    # 4. Download PDF (after transaction succeeds)
    if new_paper and new_paper.pdf_url:
        try:
            download_pdf(new_paper.pdf_url, new_paper)
        except Exception as e:
            logger.exception(f"PDF download failed for Paper ID {new_paper.id} after main task success.")
    elif new_paper:
        logger.info(f"No PDF URL found in parsed data for Paper ID {new_paper.id}. Skipping download.")

    # 5. Auto venue mapping via backend API, then Qdrant embed (best-effort).
    if new_paper:
        map_paper_venue(new_paper.id)
        embed_paper(new_paper)

    logger.info(f"Paper crawl task finished for Task ID: {task_id}")


# --- Dataset Crawling Task --- #
@shared_task(name='crawler.tasks.crawl_dataset', bind=True, max_retries=3, default_retry_delay=60)
def crawl_dataset(self, dataset_name: str, dataset_url: str | None = None, paper_id: int | None = None):
    """Celery task to crawl dataset information and create/update dataset record.
    
    Args:
        dataset_name: Name of the dataset
        dataset_url: Optional URL of the dataset page
        paper_id: Optional ID of the paper that references this dataset
    
    Returns:
        The ID of the created/updated dataset
    """
    logger.info(f"Starting dataset crawl for: {dataset_name}")
    
    try:
        # Check if dataset already exists
        dataset = Dataset.objects.filter(name=dataset_name).first()
        
        if dataset:
            logger.info(f"Found existing dataset: {dataset_name}")
            # If paper_id provided, link it to the dataset
            if paper_id:
                paper = Paper.objects.get(id=paper_id)
                dataset.papers.add(paper)
                logger.info(f"Linked paper ID {paper_id} to existing dataset {dataset_name}")
            return dataset.id
        
        # Dataset doesn't exist, create new one
        if dataset_url:
            try:
                # Fetch dataset page content
                response = requests.get(dataset_url, timeout=60)
                response.raise_for_status()
                
                # Parse dataset information
                dataset_data = parse_dataset_page(response.text, dataset_url)
                
                if dataset_data:
                    # Create new dataset with parsed information
                    dataset = Dataset.objects.create(
                        name=dataset_name,
                        description=dataset_data.get('description', ''),
                        crawled_url=dataset_data.get('crawled_url', ''),
                        url=dataset_data.get('url', ''),
                        modalities=dataset_data.get('modalities', ''),
                        languages=dataset_data.get('languages', ''),
                        licenses=dataset_data.get('licenses', ''),
                        tasks=dataset_data.get('tasks', '')
                    )
                    # Set papers after creation if paper_id exists
                    if paper_id:
                        paper = Paper.objects.get(id=paper_id)
                        dataset.papers.add(paper)
                    logger.info(f"Created new dataset with full information: {dataset_name}")
                else:
                    # Create dataset with minimal information if parsing failed
                    dataset = Dataset.objects.create(
                        name=dataset_name,
                        crawled_url=dataset_url
                    )
                    # Set papers after creation if paper_id exists
                    if paper_id:
                        paper = Paper.objects.get(id=paper_id)
                        dataset.papers.add(paper)
                    logger.warning(f"Created dataset with minimal info due to parsing failure: {dataset_name}")
            except requests.RequestException as e:
                logger.error(f"Error fetching dataset page for {dataset_name}: {e}")
                # Create dataset with minimal information
                dataset = Dataset.objects.create(
                    name=dataset_name,
                    crawled_url=dataset_url
                )
                # Set papers after creation if paper_id exists
                if paper_id:
                    paper = Paper.objects.get(id=paper_id)
                    dataset.papers.add(paper)
                logger.warning(f"Created dataset with minimal info due to fetch error: {dataset_name}")
        else:
            # Create dataset with just the name if no URL available
            dataset = Dataset.objects.create(name=dataset_name)
            # Set papers after creation if paper_id exists
            if paper_id:
                paper = Paper.objects.get(id=paper_id)
                dataset.papers.add(paper)
            logger.info(f"Created dataset with name only: {dataset_name}")
            
        return dataset.id

    except Exception as e:
        logger.exception(f"Error processing dataset {dataset_name}: {e}")
        raise self.retry(exc=e)


# --- ArXiv Integration --- #

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_BATCH_SIZE = 100
ARXIV_BATCH_DELAY = 3  # seconds between requests


def fetch_arxiv_page(start: int, max_results: int = ARXIV_BATCH_SIZE) -> str | None:
    """Fetch one page of ArXiv results for all cs.* categories."""
    params = {
        'search_query': 'cat:cs.*',
        'sortBy': 'submittedDate',
        'sortOrder': 'descending',
        'max_results': max_results,
        'start': start,
    }
    try:
        response = requests.get(ARXIV_API_URL, params=params, timeout=60)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"Error fetching ArXiv page (start={start}): {e}")
        return None


def parse_arxiv_atom(xml_content: str) -> list[dict]:
    """Parse ArXiv Atom XML feed and return list of paper dicts."""
    from datetime import datetime, timezone as dt_timezone

    ATOM = 'http://www.w3.org/2005/Atom'
    ARXIV_NS = 'http://arxiv.org/schemas/atom'
    papers = []
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.error(f"Error parsing ArXiv Atom XML: {e}")
        return papers

    for entry in root.findall(f'{{{ATOM}}}entry'):
        paper: dict = {}

        id_el = entry.find(f'{{{ATOM}}}id')
        if id_el is None or not id_el.text:
            continue
        paper['url'] = id_el.text.strip()

        arxiv_match = re.search(r'abs/(.+)$', paper['url'])
        arxiv_id = arxiv_match.group(1) if arxiv_match else None
        paper['arxiv_id'] = arxiv_id
        paper['pdf_url'] = f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else ''

        # Publisher DOI (arxiv:doi); usually absent for brand-new preprints.
        doi_el = entry.find(f'{{{ARXIV_NS}}}doi')
        paper['doi'] = doi_el.text.strip() if (doi_el is not None and doi_el.text) else None

        title_el = entry.find(f'{{{ATOM}}}title')
        paper['title'] = title_el.text.strip() if (title_el is not None and title_el.text) else '[Title Not Found]'

        summary_el = entry.find(f'{{{ATOM}}}summary')
        paper['abstract'] = summary_el.text.strip() if (summary_el is not None and summary_el.text) else ''

        published_el = entry.find(f'{{{ATOM}}}published')
        paper['publication_date'] = None
        paper['published_dt'] = None
        if published_el is not None and published_el.text:
            try:
                dt = datetime.fromisoformat(published_el.text.replace('Z', '+00:00'))
                paper['published_dt'] = dt
                paper['publication_date'] = dt.date().isoformat()
            except ValueError:
                pass

        paper['authors'] = []
        for author_el in entry.findall(f'{{{ATOM}}}author'):
            name_el = author_el.find(f'{{{ATOM}}}name')
            if name_el is not None and name_el.text:
                paper['authors'].append(name_el.text.strip())

        paper['categories'] = []
        for cat_el in entry.findall(f'{{{ATOM}}}category'):
            term = cat_el.get('term', '')
            if term:
                paper['categories'].append(term)

        # Prefer the link with title="pdf" over the fallback constructed above
        for link_el in entry.findall(f'{{{ATOM}}}link'):
            if link_el.get('title') == 'pdf' or link_el.get('type') == 'application/pdf':
                paper['pdf_url'] = link_el.get('href', paper['pdf_url'])
                break

        papers.append(paper)

    return papers


def _bare_arxiv_id(arxiv_id: str | None) -> str | None:
    """Strip the version suffix (e.g. '2605.23904v1' -> '2605.23904')."""
    if not arxiv_id:
        return None
    return re.sub(r'v\d+$', '', arxiv_id)


def arxiv_doi(arxiv_id: str | None) -> str:
    """Every arXiv submission has a DataCite DOI: 10.48550/arXiv.<bare_id>."""
    bare = _bare_arxiv_id(arxiv_id)
    return f'10.48550/arXiv.{bare}' if bare else ''


def build_arxiv_bibtex(paper: dict) -> str:
    """Generate a standard arXiv @misc BibTeX entry from parsed metadata.

    New preprints are not yet on Crossref, so we build the arXiv-style entry
    locally (matching what arxiv.org's own export produces)."""
    bare = _bare_arxiv_id(paper.get('arxiv_id'))
    if not bare:
        return ''

    authors = paper.get('authors') or []
    author_str = ' and '.join(authors)

    pub_dt = paper.get('published_dt')
    year = pub_dt.year if pub_dt else ''

    # Cite key: <firstauthorsurname><year>, falling back to arxiv<id>.
    if authors:
        surname = re.sub(r'[^A-Za-z]', '', authors[0].split()[-1]) or 'arxiv'
        cite_key = f'{surname}{year}'
    else:
        cite_key = f'arxiv{bare.replace(".", "")}'

    categories = paper.get('categories') or []
    primary_class = categories[0] if categories else ''

    fields = [
        ('title', paper.get('title', '').strip()),
        ('author', author_str),
        ('year', str(year)),
        ('eprint', bare),
        ('archivePrefix', 'arXiv'),
        ('primaryClass', primary_class),
        ('doi', arxiv_doi(paper.get('arxiv_id'))),
        ('url', f'https://arxiv.org/abs/{bare}'),
    ]
    body = ',\n'.join(f'  {k}={{{v}}}' for k, v in fields if v)
    return f'@misc{{{cite_key},\n{body}\n}}'


@shared_task(name='crawler.tasks.crawl_arxiv_new_papers')
def crawl_arxiv_new_papers():
    """Celery task: fetch today's cs.* papers from ArXiv API and save to DB."""
    logger.info("Starting ArXiv daily crawl task...")
    today = timezone.now().date()

    start = 0
    total_created = 0

    while True:
        logger.info(f"Fetching ArXiv papers batch start={start}...")
        xml_content = fetch_arxiv_page(start)
        if not xml_content:
            logger.warning("Failed to fetch ArXiv page. Stopping.")
            break

        papers = parse_arxiv_atom(xml_content)
        if not papers:
            logger.info("Empty batch returned. Stopping.")
            break

        batch_urls = [p['url'] for p in papers if 'url' in p]
        existing_task_urls = set(
            CrawlTask.objects.filter(url__in=batch_urls).values_list('url', flat=True)
        )
        existing_paper_urls = set(
            Paper.objects.filter(url__in=batch_urls).values_list('url', flat=True)
        )
        existing_urls = existing_task_urls | existing_paper_urls

        found_old = False
        for paper_data in papers:
            pub_dt = paper_data.get('published_dt')
            if pub_dt and pub_dt.date() < today:
                found_old = True
                break

            url = paper_data.get('url', '')
            if not url or url in existing_urls:
                continue

            try:
                with transaction.atomic():
                    keywords = extract_keywords_with_llm(paper_data.get('abstract', ''))
                    # Truncate to fit model field
                    keywords = keywords[:200]

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
                        category, _ = Category.objects.get_or_create(name=cat_name)
                        category_objects.append(category)

                    arxiv_doi_value = arxiv_doi(paper_data.get('arxiv_id')) or None

                    paper = Paper.objects.create(
                        title=paper_data.get('title', '[Title Not Found]'),
                        abstract=paper_data.get('abstract', ''),
                        doi=arxiv_doi_value,
                        publication_date=paper_data.get('publication_date') or None,
                        url=url,
                        pdf_url=paper_data.get('pdf_url', ''),
                        keywords=keywords,
                        file_format='pdf',
                    )

                    if author_objects:
                        paper.authors.set(author_objects)
                    if category_objects:
                        paper.categories.set(category_objects)

                    CrawlTask.objects.create(url=url, status='completed', paper=paper)

                    total_created += 1
                    logger.info(f"Saved ArXiv paper: {paper_data.get('title', '')[:80]}")

                # Venue mapping + embed outside the transaction (can be slow).
                map_paper_venue(paper.id)
                embed_paper(paper)

            except Exception as e:
                logger.exception(f"Error saving ArXiv paper {url}: {e}")

        if found_old or len(papers) < ARXIV_BATCH_SIZE:
            break

        start += ARXIV_BATCH_SIZE
        time.sleep(ARXIV_BATCH_DELAY)

    logger.info(f"ArXiv crawl finished. Created {total_created} new papers.")
    return total_created