from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid
# Create your models here.

class Paper(models.Model):
    FILE_FORMAT_CHOICES = [
        ('pdf', 'PDF'),
        ('docx', 'DOCX'),
        ('doc', 'DOC'),
        ('txt', 'TXT'),
        ('html', 'HTML'),
    ]
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    title = models.CharField(max_length=200)
    abstract = models.TextField()
    doi = models.CharField(max_length=200, null=True, blank=True)
    publication_date = models.DateField(null=True, blank=True)
    journal_or_conference = models.CharField(max_length=200)
    file_format = models.CharField(max_length=20, choices=FILE_FORMAT_CHOICES, default='pdf')
    pdf_file = models.FileField(upload_to=settings.PAPER_PDF_DIR, null=True, blank=True)
    keywords = models.CharField(max_length=200)
    url = models.URLField(max_length=200)
    pdf_url = models.URLField(max_length=200)
    github_url = models.URLField(max_length=200, null=True, blank=True)
    crawled_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    download_count = models.IntegerField(default=0)
    views_count = models.IntegerField(default=0)
    citations_count = models.IntegerField(default=0)
    references = models.ManyToManyField('self', symmetrical=False, related_name='referenced_papers')
    class Meta:
        db_table = 'papers'

class Author(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField(max_length=200)
    affiliation = models.CharField(max_length=200)
    bio = models.TextField()
    google_scholar_url = models.URLField(max_length=200)
    papers = models.ManyToManyField(Paper, related_name='authors')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'authors'

class Dataset(models.Model):
    """Represents a dataset used or mentioned in research papers."""
    name = models.CharField(
        max_length=300, 
        unique=True, # Assume dataset names should be unique 
        help_text="Name of the dataset (e.g., ImageNet, COCO, SQuAD)"
    )
    description = models.TextField(
        blank=True, 
        help_text="A brief description of the dataset."
    )
    crawled_url = models.URLField(
        max_length=500, 
        blank=True, 
        null=True
    )
    url = models.URLField(
        max_length=500, 
        blank=True, 
        null=True, 
        help_text="Homepage or download URL for the dataset."
    )
    modalities = models.CharField(
        max_length=300,
        blank=True,
        help_text="Data modalities (e.g., Image, Text, Audio, Video). Comma-separated if multiple."
    )
    languages = models.CharField(
        max_length=300,
        blank=True,
        help_text="Languages present in the dataset (e.g., English, Chinese). Comma-separated."
    )
    licenses = models.CharField(
        max_length=500, # Licenses can have long names or URLs
        blank=True,
        help_text="License(s) under which the dataset is available (e.g., CC BY-SA 4.0, MIT). Comma-separated."
    )
    tasks = models.CharField(
        max_length=500, 
        blank=True,
        help_text="Common tasks the dataset is used for (e.g., Image Classification, Question Answering). Comma-separated."
    )
    papers = models.ManyToManyField(
        Paper, 
        related_name='datasets', 
        blank=True,
        help_text="Papers that use or reference this dataset."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'datasets'
        verbose_name = "Dataset"
        verbose_name_plural = "Datasets"

class Category(models.Model):
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    dataset = models.ManyToManyField(Dataset, related_name='categories')
    papers = models.ManyToManyField(Paper, related_name='categories')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'categories'

class CrawlTask(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('crawling', 'Crawling'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    url = models.URLField(max_length=200, unique=True)
    status = models.CharField(max_length=200, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paper = models.OneToOneField(Paper, on_delete=models.CASCADE, related_name='crawl_task', null=True)
    dataset = models.OneToOneField(Dataset, on_delete=models.CASCADE, related_name='crawl_task', null=True)
    class Meta:
        db_table = 'crawl_tasks'
