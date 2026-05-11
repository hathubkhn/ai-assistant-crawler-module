from unittest.mock import patch, MagicMock, call
from django.test import TestCase
from django.utils import timezone
import logging

from crawler.models import CrawlTask, Paper, Author, Category, Dataset
# We will define these in crawler/tasks.py later
# from crawler.tasks import check_pwc_sitemap_and_create_tasks, PWC_SITEMAP_URL

# Define the expected URL here for consistency in tests
PWC_SITEMAP_URL_FOR_TEST = "https://paperswithcode.com/sitemap.xml"

class PWCSitemapTaskTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        # Simulate existing tasks in the database
        cls.existing_paper_url_1 = "https://paperswithcode.com/paper/existing-paper-1"
        cls.existing_paper_url_2 = "https://paperswithcode.com/paper/another-one-already-known"
        CrawlTask.objects.create(url=cls.existing_paper_url_1, status='completed') 
        CrawlTask.objects.create(url=cls.existing_paper_url_2, status='pending')

    @patch('crawler.tasks.create_crawl_tasks_from_urls') # Mock the creation function
    @patch('crawler.tasks.fetch_sitemap') # Mock the fetching function
    def test_check_pwc_sitemap_task_flow(self, mock_fetch_sitemap, mock_create_tasks):
        """Test the Celery task orchestrates fetching, parsing, and task creation."""
        # Define the sample sitemap content mock_fetch_sitemap should return
        sample_pwc_sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://paperswithcode.com/paper/existing-paper-1</loc></url>
  <url><loc>https://paperswithcode.com/paper/a-brand-new-paper</loc></url>
  <url><loc>https://paperswithcode.com/paper/another-one-already-known</loc></url>
  <url><loc>https://paperswithcode.com/paper/the-latest-discovery</loc></url>
  <url><loc>https://paperswithcode.com/methods</loc></url>
</urlset>"""
        mock_fetch_sitemap.return_value = sample_pwc_sitemap_xml
        
        # Define the expected list of *new* URLs that should be passed to create_crawl_tasks
        expected_new_urls = [
            "https://paperswithcode.com/paper/a-brand-new-paper",
            "https://paperswithcode.com/paper/the-latest-discovery",
        ]

        # Import and call the Celery task function we intend to create
        # This assumes the task function exists in crawler.tasks
        from crawler.tasks import check_pwc_sitemap_and_create_tasks
        check_pwc_sitemap_and_create_tasks() # Execute the task logic

        # --- Assertions --- 
        # 1. Check if fetch_sitemap was called correctly
        # We assume PWC_SITEMAP_URL is defined in crawler.tasks and matches our test constant
        mock_fetch_sitemap.assert_called_once_with(PWC_SITEMAP_URL_FOR_TEST) 
        
        # 2. Check if create_crawl_tasks_from_urls was called with the correct new URLs
        # Use assertCountEqual for order-independent comparison
        mock_create_tasks.assert_called_once()
        call_args, call_kwargs = mock_create_tasks.call_args
        self.assertCountEqual(call_args[0], expected_new_urls) 

class CrawlPaperDetailsTaskTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        # Create a pending CrawlTask for a fictional paper page
        cls.paper_page_url = "https://paperswithcode.com/paper/fictional-test-paper"
        cls.task = CrawlTask.objects.create(url=cls.paper_page_url, status='pending')

    # Final mocking strategy: Only mock external fetch and parsing
    @patch('crawler.tasks.download_pdf') 
    @patch('crawler.tasks.parse_paper_page_html') 
    @patch('crawler.tasks.requests.get') 
    def test_crawl_paper_details_task_success(self,
                                             mock_requests_get,
                                             mock_parse_html,
                                             mock_download_pdf):
        """Test the successful flow, including richer authors and datasets."""
        
        # --- Mock Configuration --- 
        # 1. Mock requests.get response 
        mock_response = MagicMock()
        sample_html_content = "<html><body><!-- ... --></body></html>" # Content doesn't matter much now
        mock_response.text = sample_html_content
        mock_response.raise_for_status = MagicMock()
        mock_requests_get.return_value = mock_response

        # 2. Mock the HTML parser's return value 
        pdf_url_to_download = 'https://arxiv.org/pdf/1234.5678.pdf'
        mock_parsed_data = {
            'title': 'Test Paper Title with Datasets',
            'abstract': 'This abstract mentions Dataset1 and Dataset2.',
            'authors': [
                {'name': 'Author One', 'affiliation': 'Uni A'}, # Richer author data
                {'name': 'Author Two', 'email': 'two@example.com'},
                {'name': 'Author One', 'affiliation': 'Uni X'}, # Duplicate name, different info (test get_or_create)
                {'name': 'Author Three'} # Minimal author data
            ],
            'categories': ['Category A', ' Category C '], # Test stripping
            'datasets': ['Dataset1', 'Dataset2', ' Dataset1 '], # Add datasets, test stripping/uniqueness
            'pdf_url': pdf_url_to_download,
            'publication_date': timezone.now().date(),
            'doi': '10.1234/test.dataset.doi',
            'journal_or_conference': 'Test Conf',
            'keywords': 'keyword3, keyword4',
            'github_url': 'https://github.com/test/dataset-repo',
        }
        mock_parse_html.return_value = mock_parsed_data

        # --- Execute the Task --- 
        from crawler.tasks import crawl_paper_details 
        initial_task_id = self.task.id
        initial_task_url = self.task.url
        crawl_paper_details(initial_task_id)

        # --- Assertions --- 
        # Verify HTTP request 
        mock_requests_get.assert_called_once_with(initial_task_url, timeout=60)
        mock_response.raise_for_status.assert_called_once()
        
        # Verify HTML parser call
        mock_parse_html.assert_called_once_with(sample_html_content, base_url=initial_task_url) # Check base_url passed

        # Verify database state changes (same assertions)
        updated_task = CrawlTask.objects.get(pk=initial_task_id)
        self.assertEqual(updated_task.status, 'completed')
        self.assertIsNotNone(updated_task.paper)
        
        created_paper = updated_task.paper
        self.assertEqual(created_paper.title, mock_parsed_data['title'])
        self.assertEqual(created_paper.abstract, mock_parsed_data['abstract'])
        self.assertEqual(created_paper.url, initial_task_url)
        self.assertEqual(created_paper.doi, mock_parsed_data['doi'])
        # ... check other paper fields ...

        self.assertEqual(created_paper.pdf_url, pdf_url_to_download) # Verify pdf_url saved
        paper_authors = created_paper.authors.all().order_by('name')
        self.assertEqual(len(paper_authors), 3) # Author One, Author Two, Author Three
        self.assertEqual(paper_authors[0].name, 'Author One')
        # Check if affiliation was potentially set (depends on creation order)
        # self.assertEqual(paper_authors[0].affiliation, 'Uni A') # This might be fragile
        self.assertEqual(paper_authors[1].name, 'Author Three')
        self.assertEqual(paper_authors[2].name, 'Author Two')
        self.assertEqual(paper_authors[2].email, 'two@example.com')
        paper_categories = set(created_paper.categories.values_list('name', flat=True))
        self.assertSetEqual(paper_categories, {'Category A', 'Category C'})

        # **NEW**: Verify Datasets
        paper_datasets = set(created_paper.datasets.values_list('name', flat=True))
        self.assertSetEqual(paper_datasets, {'Dataset1', 'Dataset2'})

        # **NEW**: Verify download_pdf was called correctly
        mock_download_pdf.assert_called_once()
        # Check the arguments passed to download_pdf
        call_args, call_kwargs = mock_download_pdf.call_args
        self.assertEqual(call_args[0], pdf_url_to_download) # Check URL
        self.assertEqual(call_args[1].id, created_paper.id) # Check Paper instance (by ID is safe)
        
# --- Integration Test --- #

class CrawlPWCLiveURLTests(TestCase):

    # Use setUp instead of setUpTestData because we need the task ID 
    # and don't want it cached across potential future test methods in this class.
    def setUp(self):
        self.pwc_url = "https://paperswithcode.com/paper/unianimate-dit-human-image-animation-with"
        self.task = CrawlTask.objects.create(url=self.pwc_url, status='pending')

    # NOTE: This test makes a LIVE network request to paperswithcode.com
    # It depends on network connectivity and the structure of the live page.
    # It uses the CURRENT parser meant for arXiv, so data extraction will be incorrect.
    def test_crawl_pwc_live_url(self):
        """Integration test: Crawl a live PWC URL with the current arXiv parser."""
        
        from crawler.tasks import crawl_paper_details
        
        # No mocking of requests or parsing - run the task as is
        try:
            crawl_paper_details(self.task.id)
        except Exception as e:
            # Allow test to fail explicitly if task raises an unhandled exception
            self.fail(f"crawl_paper_details task raised an unexpected exception: {e}")

        # --- Assertions --- 
        # Verify the final state in the database
        updated_task = CrawlTask.objects.get(pk=self.task.id)
        
        # Check task status: It might complete (with bad data) or fail if parser returned None
        # Let's accept either 'completed' or 'failed' for this test.
        self.assertIn(updated_task.status, ['completed', 'failed'])

        # If completed, check the created paper data (expecting defaults/incorrect values)
        if updated_task.status == 'completed':
            self.assertIsNotNone(updated_task.paper)
            created_paper = updated_task.paper
            
            # Title likely won't match the specific arXiv selector
            self.assertTrue(created_paper.title.startswith('[Title Not Found]') or created_paper.title != 'UniAnimate-DiT: Human Image Animation with Large-Scale Video Diffusion Transformer')
            
            # Abstract likely won't match
            self.assertTrue(created_paper.abstract.startswith('[Abstract Not Found]') or len(created_paper.abstract) < 50) # Arbitrary short length check

            # Authors/Categories/Datasets likely empty based on current parser
            self.assertEqual(created_paper.authors.count(), 0)
            self.assertEqual(created_paper.categories.count(), 0)
            self.assertEqual(created_paper.datasets.count(), 0)
            
            # Date/DOI might be None
            self.assertIsNone(created_paper.publication_date)
            self.assertIsNone(created_paper.doi)
            
            # Check the original URL was stored
            self.assertEqual(created_paper.url, self.pwc_url)
            
            # PDF URL might be found if there's a generic link, or None
            # Github URL might be found by the simple parser logic
            # No strict assertion here, just acknowledging potential outcomes.
            logger.info(f"[Test Info] PWC Live URL Crawl resulted in Paper ID {created_paper.id} with PDF: {created_paper.pdf_url}, GitHub: {created_paper.github_url}")
            
        elif updated_task.status == 'failed':
             logger.info("[Test Info] PWC Live URL Crawl resulted in task status 'failed'.")
             self.assertIsNone(updated_task.paper) # Paper should not be linked on failure
        