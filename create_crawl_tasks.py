# [PWC DISABLED] Shared DB has no `crawl_tasks` table (CrawlTask). PWC crawl is not
# implemented on the shared database yet. Use `python manage.py crawl_arxiv` instead.
#
# Original script: read PWC paper URLs from CSV and enqueue CrawlTask rows.
# Re-enable when PWC is refactored (dedup via Paper.url, ArXiv pattern).

raise SystemExit(
    "PWC crawl disabled: shared DB has no crawl_tasks table. "
    "Use: python manage.py crawl_arxiv"
)

# --- preserved for reference (not executed) ---
# import csv
# import os
# import django
# from django.db import transaction
# from tqdm import tqdm
#
# os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crawler_service.settings')
# django.setup()
#
# from crawler.models import CrawlTask
# from crawler.tasks import crawl_paper_details
# ...
