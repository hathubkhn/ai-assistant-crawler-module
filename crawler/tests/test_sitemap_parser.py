import xml.etree.ElementTree as ET
from unittest.mock import patch
from django.test import TestCase
from crawler.models import CrawlTask, Paper # Assuming Paper might be needed later if CrawlTask requires it

# Import the actual parser function
from crawler.parsers import parse_pwc_sitemap_for_new_papers

class PWCSitemapParserTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        # Simulate existing tasks in the database
        cls.existing_paper_url_1 = "https://paperswithcode.com/paper/existing-paper-1"
        cls.existing_paper_url_2 = "https://paperswithcode.com/paper/another-one-already-known"
        
        # Note: CrawlTask has a OneToOneField to Paper. 
        # In a real scenario, creating a CrawlTask might require a Paper instance, 
        # or the OneToOneField needs to allow null=True initially.
        # For this test, let's assume we can create CrawlTask with just a URL for now,
        # or that the relationship allows null.
        # If this fails, we might need to create dummy Paper objects.
        CrawlTask.objects.create(url=cls.existing_paper_url_1, status='completed') # Assuming status doesn't matter for filtering
        CrawlTask.objects.create(url=cls.existing_paper_url_2, status='pending')

    def test_parse_new_paper_urls(self):
        """Test extracting new paper URLs from PWC sitemap XML."""
        
        sample_pwc_sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://paperswithcode.com/paper/existing-paper-1</loc>
    <lastmod>2024-01-01</lastmod>
  </url>
  <url>
    <loc>https://paperswithcode.com/paper/a-brand-new-paper</loc>
    <lastmod>2024-02-01</lastmod>
  </url>
  <url>
    <loc>https://paperswithcode.com/paper/another-one-already-known</loc>
    <lastmod>2023-12-01</lastmod>
  </url>
  <url>
    <loc>https://paperswithcode.com/paper/the-latest-discovery</loc>
    <lastmod>2024-02-15</lastmod>
  </url>
  <url>
    <loc>https://paperswithcode.com/methods</loc> 
    </url>
  <url>
    <loc>https://paperswithcode.com/about</loc> 
    </url>
</urlset>"""

        expected_new_urls = [
            "https://paperswithcode.com/paper/a-brand-new-paper",
            "https://paperswithcode.com/paper/the-latest-discovery",
        ]

        # Call the actual parser function
        extracted_new_urls = parse_pwc_sitemap_for_new_papers(sample_pwc_sitemap_xml)
        
        self.assertCountEqual(extracted_new_urls, expected_new_urls) 

    def test_empty_sitemap(self):
        """Test parsing an empty but valid sitemap."""
        empty_sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
</urlset>"""
        extracted_new_urls = parse_pwc_sitemap_for_new_papers(empty_sitemap_xml)
        self.assertEqual(extracted_new_urls, [])

    def test_sitemap_with_no_paper_urls(self):
        """Test parsing a sitemap with URLs, but none for papers."""
        no_papers_sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://paperswithcode.com/methods</loc></url>
  <url><loc>https://paperswithcode.com/about</loc></url>
  <url><loc>https://example.com/paper/some-other-site</loc></url> 
</urlset>"""
        extracted_new_urls = parse_pwc_sitemap_for_new_papers(no_papers_sitemap_xml)
        self.assertEqual(extracted_new_urls, [])

    def test_sitemap_with_all_known_urls(self):
        """Test parsing where all paper URLs already exist in CrawlTask."""
        all_known_sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{self.existing_paper_url_1}</loc></url>
  <url><loc>{self.existing_paper_url_2}</loc></url>
  <url><loc>https://paperswithcode.com/methods</loc></url>
</urlset>"""
        # Ensure these URLs exist from setUpTestData
        extracted_new_urls = parse_pwc_sitemap_for_new_papers(all_known_sitemap_xml)
        self.assertEqual(extracted_new_urls, [])

    def test_malformed_xml_sitemap(self):
        """Test parsing malformed XML content."""
        malformed_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://paperswithcode.com/paper/good-one</loc></url>
  <url><loc>https://paperswithcode.com/paper/bad-one</loc>
</urlset> <!-- Missing closing </url> tag -->"""
        # Expect the function to handle the ParseError gracefully and return empty list
        # Also expect an error log message (though we don't typically assert logs in unit tests)
        extracted_new_urls = parse_pwc_sitemap_for_new_papers(malformed_xml)
        self.assertEqual(extracted_new_urls, []) 