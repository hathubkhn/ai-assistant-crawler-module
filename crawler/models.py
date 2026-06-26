from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid
# NOTE: This service writes into the SHARED database `research_paper_dev`,
# whose tables are owned/created by another app (public_api). All models here
# are therefore `managed = False` so Django never tries to migrate/ALTER them.
# Field definitions are mapped to the REAL columns of the existing tables.


class Paper(models.Model):
    FILE_FORMAT_CHOICES = [
        ('pdf', 'PDF'),
        ('docx', 'DOCX'),
        ('doc', 'DOC'),
        ('txt', 'TXT'),
        ('html', 'HTML'),
    ]
    # Real PK is a UUID column named `id` (no separate `uuid` column exists).
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=512)
    abstract = models.TextField()
    doi = models.CharField(max_length=200, null=True, blank=True)
    publication_date = models.DateField(null=True, blank=True)
    file_format = models.CharField(max_length=20, choices=FILE_FORMAT_CHOICES, default='pdf')
    pdf_file = models.FileField(upload_to=settings.PAPER_PDF_DIR, null=True, blank=True)
    # `keywords` is a jsonb NOT NULL column -> store a JSON list, default [].
    keywords = models.JSONField(default=list)
    url = models.URLField(max_length=500)
    pdf_url = models.URLField(max_length=500)
    github_url = models.URLField(max_length=500, null=True, blank=True)
    # NOT NULL text columns present in the real table; default '' so inserts succeed.
    method = models.TextField(default='')
    results = models.TextField(default='')
    conclusions = models.TextField(default='')
    bibtex = models.TextField(default='')
    download_count = models.IntegerField(default=0)
    views_count = models.IntegerField(default=0)
    citations_count = models.IntegerField(default=0)
    crawled_at = models.DateTimeField(default=timezone.now)
    embedded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # conference_id / journal_id (uuid, nullable) exist in the table but are not
    # populated by the ArXiv crawl; omitted here so they default to NULL.
    references = models.ManyToManyField('self', symmetrical=False, related_name='referenced_papers')

    class Meta:
        managed = False
        db_table = 'papers'


class Author(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField(max_length=200)
    affiliation = models.CharField(max_length=200)
    bio = models.TextField()
    google_scholar_url = models.URLField(max_length=200)
    # Real M2M table `authors_papers` has columns (author_id, paper_id) which
    # match Django's auto-generated through naming, so no explicit through needed.
    papers = models.ManyToManyField(Paper, related_name='authors')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'authors'


class Category(models.Model):
    """Maps to the existing `tasks` table (PapersWithCode "tasks" == categories)."""
    name = models.CharField(max_length=200)
    description = models.TextField(default='')
    # M2M via `tasks_papers`; explicit through needed because the FK column is
    # `task_id` (not the default `category_id`).
    papers = models.ManyToManyField(Paper, through='TaskPaper', related_name='categories')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'tasks'


class TaskPaper(models.Model):
    """Through model for Category(tasks) <-> Paper via the real `tasks_papers` table."""
    task = models.ForeignKey(Category, db_column='task_id', on_delete=models.CASCADE)
    paper = models.ForeignKey(Paper, db_column='paper_id', on_delete=models.CASCADE)

    class Meta:
        managed = False
        db_table = 'tasks_papers'


class Dataset(models.Model):
    """[PWC DISABLED] Stub for Papers With Code dataset crawl only.

    Shared DB uses `public_api_dataset` (backend schema), not `datasets`.
    Not used while PWC crawl is disabled (no CrawlTask / crawl_tasks table).
    """
    name = models.CharField(max_length=300, unique=True)
    description = models.TextField(blank=True)
    crawled_url = models.URLField(max_length=500, blank=True, null=True)
    url = models.URLField(max_length=500, blank=True, null=True)
    modalities = models.CharField(max_length=300, blank=True)
    languages = models.CharField(max_length=300, blank=True)
    licenses = models.CharField(max_length=500, blank=True)
    tasks = models.CharField(max_length=500, blank=True)
    papers = models.ManyToManyField(Paper, related_name='datasets', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        managed = False
        db_table = 'datasets'


class CrawlTask(models.Model):
    """[PWC DISABLED] Stub for PWC crawl orchestration only.

    There is NO `crawl_tasks` table in the shared DB — do not query in production.
    ArXiv crawl deduplicates on `Paper.url` instead. Kept for tests / future refactor.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('crawling', 'Crawling'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    url = models.URLField(max_length=500, unique=True)
    status = models.CharField(max_length=200, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paper = models.OneToOneField(Paper, on_delete=models.CASCADE, related_name='crawl_task', null=True)
    dataset = models.OneToOneField(Dataset, on_delete=models.CASCADE, related_name='crawl_task', null=True)

    class Meta:
        managed = False
        db_table = 'crawl_tasks'
