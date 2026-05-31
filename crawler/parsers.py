
import logging
import re
import time
from bs4 import BeautifulSoup
import requests
from typing import List
from datetime import datetime

# Configure logger for this module
logger = logging.getLogger(__name__)

# Define the expected domain and path prefix for PWC papers
PWC_DOMAIN = "paperswithcode.com"
PWC_PAPER_PATH_PREFIX = "/paper/"
PWC_BASE_URL = f'https://{PWC_DOMAIN}'

# --- PWC Sitemap Parsing --- #
def fetch_sitemap_content(sitemap_url: str) -> str:
    """Fetches sitemap content from given URL."""
    try:
        response = requests.get(sitemap_url)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"Error fetching sitemap from {sitemap_url}: {e}")
        return ""

def extract_metrics(soup: BeautifulSoup) -> dict[str, int]:
    """Extract metrics like download count, views, and citations."""
    metrics = {
        'download_count': 0,
        'views_count': 0,
        'citations_count': 0
    }
    
    try:
        # Look for metrics in various common formats
        metrics_div = soup.find('div', class_=lambda x: x and ('metrics' in x.lower() or 'stats' in x.lower()))
        if metrics_div:
            # Extract numbers from text using regex
            text = metrics_div.get_text()
            downloads = re.search(r'(\d+)\s*downloads?', text, re.I)
            views = re.search(r'(\d+)\s*views?', text, re.I)
            citations = re.search(r'(\d+)\s*citations?', text, re.I)
            
            if downloads:
                metrics['download_count'] = int(downloads.group(1))
            if views:
                metrics['views_count'] = int(views.group(1))
            if citations:
                metrics['citations_count'] = int(citations.group(1))
    except Exception as e:
        logger.warning(f"Error extracting metrics: {e}")
    
    return metrics

def extract_keywords(soup: BeautifulSoup) -> list[str]:
    """Extract keywords from meta tags or dedicated sections."""
    keywords = []
    try:
        # Check meta tags first
        meta_keywords = soup.find('meta', {'name': ['keywords', 'Keywords']})
        if meta_keywords and meta_keywords.get('content'):
            keywords = [k.strip() for k in meta_keywords['content'].split(',')]
        
        # Look for dedicated keywords section
        if not keywords:
            keywords_section = soup.find(['div', 'section'], 
                                      string=re.compile(r'keywords?', re.I))
            if keywords_section:
                text = keywords_section.get_text()
                # Remove "Keywords:" prefix if present
                text = re.sub(r'^keywords?:\s*', '', text, flags=re.I)
                keywords = [k.strip() for k in text.split(',')]
    except Exception as e:
        logger.warning(f"Error extracting keywords: {e}")
    
    return keywords

def extract_datasets(soup: BeautifulSoup) -> list[dict[str, str]]:
    """Extract dataset information from the paper page.
    
    Args:
        soup: BeautifulSoup object of the paper page
        
    Returns:
        List of dictionaries containing dataset information:
        - name: Dataset name
        - url: Full URL to the dataset page on Papers with Code
        - image_url: URL to dataset thumbnail image (if available)
    """
    datasets = []
    try:
        # Find the datasets section
        datasets_section = soup.find('div', class_='paper-datasets')
        if datasets_section:
            # Find all dataset badges
            dataset_badges = datasets_section.find_all('span', class_='badge badge-primary')
            
            for badge in dataset_badges:
                dataset_link = badge.find('a')
                if dataset_link and dataset_link.get('href'):
                    dataset_info = {
                        'name': dataset_link.get_text(strip=True),
                        'url': f"https://paperswithcode.com{dataset_link['href']}"
                    }
                    
                    # Try to get dataset image URL if available
                    dataset_img = dataset_link.find('img', class_='dataset-list-image')
                    if dataset_img and dataset_img.get('src'):
                        dataset_info['image_url'] = dataset_img['src']
                    
                    datasets.append(dataset_info)
                    
        if not datasets:
            logger.warning("No datasets found in the paper-datasets section")
            
    except Exception as e:
        logger.warning(f"Error extracting datasets: {e}")
    
    return datasets

def extract_authors(soup: BeautifulSoup) -> list[str]:
    """Extract authors from the page."""
    # Authors
    authors_list = []
    try:
        authors_div = soup.find('div', class_='authors')
        if authors_div:
            author_tags = authors_div.find_all('a')
            authors_list = [a.get_text(strip=True) for a in author_tags]

        if not authors_list:
            logger.warning("Could not find author tags.")
    except Exception as e:
        logger.warning(f"Error extracting authors: {e}")
    return authors_list

def extract_title(soup: BeautifulSoup) -> str:
    """Extract title from the page."""
    title_div = soup.find('div', class_='paper-title')
    if title_div:
        title_h1 = title_div.find('h1')
        if title_h1:
            return title_h1.get_text(strip=True)
        else:
            logger.warning("Could not find title tag.")
            return '[Title Not Found]'
    else:
        logger.warning("Could not find title tag.")
        return '[Title Not Found]'
    
def extract_abstract(soup: BeautifulSoup) -> str:
    """Extract abstract from the page."""
    abstract_div = soup.find('div', class_='paper-abstract')
    if abstract_div:
        abstract_p = abstract_div.find('p')
        if abstract_p:
            return abstract_p.get_text(strip=True)
        else:
            logger.warning("Could not find abstract tag.")
            return '[Abstract Not Found]'
    else:
        logger.warning("Could not find abstract tag.")
        return '[Abstract Not Found]'

def extract_pdf_urls(soup: BeautifulSoup) -> dict[str, str]:
    """Extract PDF URLs from the paper page.
    Args:
        soup: BeautifulSoup object of the paper page
    Returns:
        Dictionary containing PDF URLs with their sources as keys
        e.g. {'arxiv': 'https://arxiv.org/pdf/...', 'conference': 'http://papers.nips.cc/...'}
    """
    pdf_urls = {}
    try:
        # Find all PDF links in the paper-abstract div
        abstract_div = soup.find('div', class_='paper-abstract')
        if abstract_div:
            # Find all links with PDF icon/text
            all_a_tags = abstract_div.find_all('a')
            for link in all_a_tags:
                icon_span = link.find('span', attrs={'data-name': 'file-pdf'})
                text_span = link.find('span', string=lambda s: s and s.strip().upper() == 'PDF')
                if not (icon_span and text_span):
                    continue
                href = link.get('href', '')
                if not href:
                    continue
                    
                # Check if it's a PDF link (has PDF icon or text)
                pdf_icon = link.find('span', class_='icon-fa', attrs={'data-name': 'file-pdf'})
                link_text = link.get_text(strip=True)
                
                if pdf_icon or 'PDF' in link_text:
                    # Determine source based on URL or link text
                    if 'arxiv.org' in href:
                        pdf_urls['arxiv'] = href
                    elif 'papers.nips.cc' in href or 'NeurIPS' in link_text:
                        pdf_urls['conference'] = href
                    else:
                        pdf_urls['other'] = href
                        
        # Prioritize arXiv if available
        if 'arxiv' in pdf_urls:
            pdf_urls['primary'] = pdf_urls['arxiv']
        elif 'conference' in pdf_urls:
            pdf_urls['primary'] = pdf_urls['conference']
        elif 'other' in pdf_urls:
            pdf_urls['primary'] = pdf_urls['other']
            
    except Exception as e:
        logger.warning(f"Error extracting PDF URLs: {e}")
        
    return pdf_urls

def extract_arxiv_doi(url: str) -> str | None:
    """Extract DOI from ArXiv abstract page.
    
    Args:
        arxiv_url: URL to the ArXiv abstract page (not PDF)
        
    Returns:
        DOI string if found, None otherwise
    """
    try:
        if url is None:
            return None
        
        arxiv_url = url.replace('/pdf/', '/abs/').rstrip('.pdf')
        
        if arxiv_url is None:
            return None
            
        response = requests.get(arxiv_url, timeout=30)
        response.raise_for_status()
        
        for parser in ['lxml', 'html.parser', 'html5lib']:
            try:
                soup = BeautifulSoup(response.text, parser)
                doi_cell = soup.find('td', class_='tablecell arxivdoi')
                if doi_cell:
                    doi_link = doi_cell.find('a', id='arxiv-doi-link')
                    if doi_link and doi_link.get('href'):
                        doi_match = re.search(r'doi\.org/(.+)$', doi_link['href'])
                        if doi_match:
                            return doi_match.group(1)    
                logger.warning(f"Could not find DOI on ArXiv page: {arxiv_url}")
            except Exception as e:
                logger.warning(f"Parser {parser} failed: {e}")
                continue
        return None
        
    except Exception as e:
        logger.warning(f"Error fetching/parsing ArXiv page {arxiv_url}: {e}")
        return None

def extract_arxiv_submission_date(soup: BeautifulSoup) -> str | None:
    """Extract submission date from the page."""
    title_div = soup.find('div', class_='paper-title')
    if not title_div:
        return None
    author_div = title_div.find('div', class_='authors')
    if not author_div:
        return None
    author_spans_text = author_div.find('span', class_='author-span').get_text(strip=True)
    match = re.search(r'(\d{1,2}\s+\w{3}\s+\d{4})', author_spans_text)
    if match: 
        submission_date_str = match.group(1)
        try:
            # Parse the date using datetime
            date_obj = datetime.strptime(submission_date_str, '%d %b %Y')
            # Convert to YYYY-MM-DD format
            return date_obj.strftime('%Y-%m-%d')
        except ValueError as e:
            logger.warning(f"Could not parse date string: {submission_date_str}. Error: {e}")
            return None
    return None

def extract_conference_name(soup: BeautifulSoup) -> str | None:
    """Extract conference name from the page."""
    title_div = soup.find('div', class_='paper-title')
    if not title_div:
        return None
    author_div = title_div.find('div', class_='authors')
    if not author_div:
        return None
    conference_span = author_div.find('span', class_='item-conference-link')
    if conference_span:
        return conference_span.get_text(strip=True)
    return None

def extract_categories(soup: BeautifulSoup) -> list[str]:
    """Extract categories from the page."""
    task_div = soup.find('div', id='tasks')
    if not task_div:
        return []
    
    paper_task_div = task_div.find('div', class_='paper-tasks')
    all_task_spans = paper_task_div.find_all('a')
    all_task_names = [span.get_text(strip=True) for span in all_task_spans]
    if not all_task_names:
        logger.warning("Could not find subjects/categories.")
    return all_task_names

def extract_github_url(soup: BeautifulSoup) -> str | None:
    """Extract GitHub URL from the page."""
    github_link = soup.find('a', href=lambda href: href and 'github.com/' in href)
    return github_link['href'] if github_link else None

def parse_paper_page_html(html_content: str, base_url: str | None = None) -> dict | None:
    """Parses the HTML content of a paper page (e.g., arXiv abstract page).
    Currently targets arXiv abstract page structure.
    
    Args:
        html_content: The raw HTML string.
        base_url: The original URL the HTML was fetched from, for resolving relative links.

    Returns:
        A dictionary containing extracted paper details or None if parsing fails.
    """
    logger.info("Attempting to parse paper page HTML...")
    try:
        soup = BeautifulSoup(html_content, 'lxml')
        data = {}

        # --- Extract Data (arXiv specific selectors - adjust if needed) ---

        data = {
            'title': extract_title(soup),
            'abstract': extract_abstract(soup),
            'authors': extract_authors(soup),
            'publication_date': extract_arxiv_submission_date(soup),
            'categories': extract_categories(soup),
            'journal_or_conference': extract_conference_name(soup),
            'keywords': extract_keywords(soup),    
            'github_url': extract_github_url(soup),
            'datasets': extract_datasets(soup),
            **extract_metrics(soup),
        }
        data['pdf_url'] = extract_pdf_urls(soup).get('primary')
        data['doi'] = extract_arxiv_doi(data['pdf_url'])
        # File format (default to PDF)
        data['file_format'] = 'pdf'

        # Original URL
        data['url'] = base_url if base_url else None

        logger.info(f"Successfully parsed paper page. Title: {data.get('title')}")
        return data

    except Exception as e:
        logger.exception("An unexpected error occurred during HTML parsing.")
        return None 


# --- Dataset Parsing --- #
def extract_dataset_name(soup: BeautifulSoup) -> str:
    """Extract dataset name from the page."""
    try:
        # Look for dataset name in title
        title_div = soup.find('div', class_='dataset-title mb-3')
        if not title_div:
            return '[Dataset Name Not Found]'
        
        title_h1 = title_div.find('h1')
        if not title_h1:
            return '[Dataset Name Not Found]'
        return title_h1.get_text(strip=True)
    except Exception as e:
        logger.warning(f"Error extracting dataset name: {e}")
        return '[Dataset Name Not Found]'

def extract_dataset_description(soup: BeautifulSoup) -> str:
    """Extract dataset description from the page."""
    try:
        # Try meta description first
        meta_desc = soup.find('div', class_='description-content')
        if not meta_desc:
            return ""
        
        # Look for description in main content
        desc_section = meta_desc.find('p')
        if not desc_section:
            return ""
        return desc_section.get_text(strip=True)
    except Exception as e:
        logger.warning(f"Error extracting dataset description: {e}")
    return ""

def extract_dataset_url(soup: BeautifulSoup) -> str | None:
    """Extract dataset URL from the page."""
    try:
        # Look for dataset URL in the page content
        description_div = soup.find('div', class_='description-content')
        if not description_div:
            return None
        homepage = description_div.find_next_sibling()
        if not homepage:
            return None
        dataset_url = homepage.find('a', class_='btn btn-primary-outline dataset-homepage')
        if not dataset_url:
            return None
        return dataset_url['href']
    except Exception as e:
        logger.warning(f"Error extracting dataset URL: {e}")
        return None

def extract_dataset_modalities(soup: BeautifulSoup) -> list[str]:
    """Extract dataset modalities from the page."""

    try:
        # Look for common modality keywords in the page content
        modality_div = soup.find(lambda tag: tag.name == 'div' and
                                    tag.get('class') == ['collections'] and
                                    'Modalities' in tag.get_text())
        if not modality_div:
            return []
        modality_div = modality_div.find_next_sibling()
        if not modality_div:
            return []
        modalities = [span.get_text(strip=True) for span in modality_div.find_all('li')]
        return modalities
    except Exception as e:
        logger.warning(f"Error extracting dataset modalities: {e}")
        return []

def extract_dataset_languages(soup: BeautifulSoup) -> list[str]:
    """Extract dataset languages from the page."""
    languages = set()
    try:
        dataset_info_div = soup.find('div', class_='col-md-3 dataset-infobox')
        if not dataset_info_div:
            return []
            
        language_div = dataset_info_div.find('div', class_='languages')
        if not language_div:
            return []
            
        # Get the next element after the languages div (should be the ul containing languages)
        language_list = language_div.find_next_sibling()
        if not language_list:
            return []
            
        # Extract languages from li elements
        languages = [language.get_text(strip=True) for language in language_list.find_all('li')]
        return list(set(filter(None, languages)))  # Remove duplicates and empty strings
        
    except Exception as e:
        logger.warning(f"Error extracting dataset languages: {e}")
        return []

def extract_dataset_licenses(soup: BeautifulSoup) -> list[str]:
    """Extract dataset licenses from the page."""
    try:
        # Look for common license mentions
        license_div = soup.find('div', class_='license')
        if not license_div:
            return []
        
        license_list = license_div.find('ul', class_='list-unstyled')
        licenses = license_list.find_all('li')
        if not licenses:
            return []
        licenses = [license.get_text(strip=True) for license in licenses]
        return licenses

    except Exception as e:
        logger.warning(f"Error extracting dataset licenses: {e}")
        return []

def extract_dataset_tasks(soup: BeautifulSoup) -> list[str]:
    """Extract common tasks the dataset is used for."""
    try:
        task_div = soup.find('div', class_='col-md-3 dataset-infobox')
        task_div = task_div.find(lambda tag: tag.name=="div" and "Tasks" in tag.get_text())
        if not task_div:
            return []
        task_div = task_div.find_next_sibling()
        if not task_div:
            return []
        tasks = [span.get_text(strip=True) for span in task_div.find_all('li')]
        return tasks
    except Exception as e:
        logger.warning(f"Error extracting dataset tasks: {e}")
        return []

def extract_keywords_with_llm(abstract: str) -> str:
    """Extract keywords from abstract using an OpenAI-compatible LLM endpoint
    (defaults to the internal vLLM server — see settings.OPENAI_BASE_URL).

    Returns comma-separated keywords string, or '' on failure/skip.
    """
    from django.conf import settings
    from openai import OpenAI

    if not abstract or len(abstract) < 50:
        return ''

    model = getattr(settings, 'OPENAI_MODEL', '')
    if not model:
        logger.warning("OPENAI_MODEL not set. Skipping LLM keyword extraction.")
        return ''

    base_url = getattr(settings, 'OPENAI_BASE_URL', None)
    # vLLM does not enforce the API key, but the OpenAI SDK refuses an empty string.
    api_key = getattr(settings, 'OPENAI_API_KEY', '') or 'EMPTY'
    truncated = abstract[:1000]

    prompt = (
        "Given the following research paper abstract, extract 8-10 keywords "
        "that best represent the core topics, methods, and contributions. "
        "Return as a comma-separated list of short phrases (1-3 words each). "
        "Normalize to standard academic terms where possible.\n\n"
        f"Abstract: {truncated}"
    )

    client = OpenAI(api_key=api_key, base_url=base_url)
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"LLM keyword extraction attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)

    return ''


def parse_dataset_page(html_content: str, url: str) -> dict:
    """Parse a dataset page and extract all relevant information.
    
    Args:
        html_content: The raw HTML string of the dataset page
        url: The URL of the dataset page
        
    Returns:
        A dictionary containing all extracted dataset fields
    """
    try:
        soup = BeautifulSoup(html_content, 'lxml')
        
        dataset_info = {
            'name': extract_dataset_name(soup),
            'description': extract_dataset_description(soup),
            'crawled_url': url,
            'url': extract_dataset_url(soup),
            'modalities': extract_dataset_modalities(soup),
            'languages': extract_dataset_languages(soup),
            'licenses': extract_dataset_licenses(soup),
            'tasks': extract_dataset_tasks(soup),
            'papers': []
        }
        
        # Log extraction results
        logger.info(f"Successfully parsed dataset page: {url}")
        for key, value in dataset_info.items():
            if key != 'description':  # Skip long description in logs
                logger.debug(f"Extracted {key}: {value}")
                
        return dataset_info
        
    except Exception as e:
        logger.error(f"Error parsing dataset page {url}: {e}")
        return None 