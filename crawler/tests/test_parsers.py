import os
from datetime import date
from django.test import TestCase

# Use absolute import instead of relative
from crawler.parsers import parse_paper_page_html

# Helper to load sample HTML from a file
# (You might need to create these HTML files in a test_data directory)
# def load_sample_html(filename):
#     dir_path = os.path.dirname(os.path.realpath(__file__))
#     file_path = os.path.join(dir_path, 'test_data', filename)
#     try:
#         with open(file_path, 'r', encoding='utf-8') as f:
#             return f.read()
#     except FileNotFoundError:
#         return None 

class ParsePaperPageHTMLTests(TestCase):

    def test_parse_arxiv_success(self):
        """Test parsing a typical arXiv abstract page successfully."""
        # Sample HTML mimicking arXiv structure (simplified)
        # Ideally, load this from a file for larger samples
        sample_arxiv_html = """
        <!DOCTYPE html>
        <html>
        <head><title>Test Paper</title></head>
        <body>
            <h1 class="title" itemprop="name">
                <span class="title-span">Test Paper Title: A Study</span>
            </h1>
            <div class="authors">
                <a href="/find/cs/1/au:+One_A/0/1/0/all/0/1">Author One</a>, 
                <a href="/find/cs/1/au:+Two_B/0/1/0/all/0/1">Author Two</a>
            </div>
            <blockquote class="abstract mathjax">
                Abstract: This is the abstract of the test paper. It contains details.
            </blockquote>
            <div class="dateline">
                (Submitted on 15 Feb 2024)
            </div>
             <div class="submission-history"><b>[v1]</b> Tue, 13 Feb 2024 10:00:00 UTC (1,234 KB)</div>
            <td class="tablecell subjects">
                Computer Science - Artificial Intelligence (cs.AI); Computation and Language (cs.CL)
            </td>
            <td class="tablecell doi">
                <a href="https://doi.org/10.1234/test.doi.value">10.1234/test.doi.value</a>
            </td>
            <div class="full-text">
                <ul>
                    <li><a href="/pdf/2402.12345.pdf" class="abs-button download-pdf" type="application/pdf">PDF</a></li>
                    <li><a href="https://github.com/test/repo">GitHub Link</a></li>
                </ul>
            </div>
        </body>
        </html>
        """
        base_url = "https://arxiv.org/abs/2402.12345" # Example base URL
        
        expected_data = {
            'title': 'Test Paper Title: A Study',
            'abstract': 'This is the abstract of the test paper. It contains details.',
            'authors': ['Author One', 'Author Two'],
            'categories': ['Computer Science - Artificial Intelligence (cs.AI)', 'Computation and Language (cs.CL)'],
            'pdf_url': 'https://arxiv.org/pdf/2402.12345.pdf', # Resolved URL
            'publication_date': date(2024, 2, 13), # Parsed from submission history
            'doi': '10.1234/test.doi.value',
            'journal_or_conference': None, # Not typically on arXiv abstract
            'keywords': None, # Not typically parsed here
            'github_url': 'https://github.com/test/repo',
        }

        parsed_data = parse_paper_page_html(sample_arxiv_html, base_url=base_url)

        self.assertIsNotNone(parsed_data)
        self.assertDictEqual(parsed_data, expected_data)

    # Add more test cases below for edge cases and missing elements
    def test_parse_missing_elements(self):
        """Test parsing HTML with missing abstract, DOI, and authors."""
        sample_html_missing = """
        <html><body>
            <h1 class="title"><span>Minimal Paper</span></h1>
            <div class="submission-history"><b>[v1]</b> Mon, 1 Jan 2024 00:00:00 UTC</div>
            <div class="full-text"><ul><li><a href="/pdf/2401.00001.pdf">PDF</a></li></ul></div>
        </body></html>
        """
        base_url = "https://arxiv.org/abs/2401.00001"
        parsed_data = parse_paper_page_html(sample_html_missing, base_url=base_url)
        
        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data['title'], 'Minimal Paper')
        self.assertEqual(parsed_data['abstract'], '[Abstract Not Found]') # Check default
        self.assertEqual(parsed_data['authors'], [])
        self.assertEqual(parsed_data['categories'], [])
        self.assertEqual(parsed_data['pdf_url'], 'https://arxiv.org/pdf/2401.00001.pdf')
        self.assertEqual(parsed_data['publication_date'], date(2024, 1, 1))
        self.assertIsNone(parsed_data['doi'])
        self.assertIsNone(parsed_data['github_url'])

    def test_parse_no_base_url_relative_pdf(self):
        """Test parsing with a relative PDF link but no base_url provided."""
        sample_html_relative = """
        <html><body>
            <h1 class="title"><span>Relative PDF Test</span></h1>
            <div class="full-text"><ul><li><a href="/pdf/relative.pdf">PDF</a></li></ul></div>
        </body></html>
        """
        parsed_data = parse_paper_page_html(sample_html_relative, base_url=None)
        self.assertIsNotNone(parsed_data)
        # Expect pdf_url to be None because it couldn't be resolved
        self.assertIsNone(parsed_data['pdf_url']) 

    def test_parse_absolute_pdf_url(self):
        """Test parsing with an absolute PDF URL."""
        absolute_pdf = "https://example.com/path/to/paper.pdf"
        sample_html_absolute = f"""
        <html><body>
            <h1 class="title"><span>Absolute PDF Test</span></h1>
            <div class="full-text"><ul><li><a href="{absolute_pdf}">PDF</a></li></ul></div>
        </body></html>
        """
        parsed_data = parse_paper_page_html(sample_html_absolute, base_url="https://irrelevant.com")
        self.assertIsNotNone(parsed_data)
        self.assertEqual(parsed_data['pdf_url'], absolute_pdf)

    def test_parse_invalid_date(self):
        """Test parsing with an unparseable date format."""
        sample_html_bad_date = """
        <html><body>
            <h1 class="title"><span>Bad Date Test</span></h1>
             <div class="submission-history"><b>[v1]</b> Bad Date String 2024</div>
        </body></html>
        """
        parsed_data = parse_paper_page_html(sample_html_bad_date)
        self.assertIsNotNone(parsed_data)
        # Expect publication_date to be None due to parsing error
        self.assertIsNone(parsed_data['publication_date']) 