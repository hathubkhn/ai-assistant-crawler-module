import json
from django.core.management.base import BaseCommand
from django.core import serializers
from crawler.models import Paper, Author, Dataset, Category, CrawlTask
import os
from tqdm import tqdm

class Command(BaseCommand):
    help = 'Export all database records to JSON files'

    def handle(self, *args, **options):
        # Create exports directory if it doesn't exist
        if not os.path.exists('exports'):
            os.makedirs('exports')

        # Dictionary mapping model classes to their table names
        models = {
            Paper: 'papers',
            Author: 'authors',
            Dataset: 'datasets',
            Category: 'categories',
            CrawlTask: 'crawl_tasks'
        }

        total_records = 0
        for model in models.keys():
            total_records += model.objects.count()

        self.stdout.write(f'Found {total_records} total records across all tables')
        
        for model, table_name in models.items():
            records = model.objects.all()
            count = records.count()
            
            if count == 0:
                self.stdout.write(f'Skipping {table_name} - no records found')
                continue
                
            self.stdout.write(f'\nProcessing {count} records from {table_name}...')
            
            # Initialize progress bar
            pbar = tqdm(total=count, desc=f'Exporting {table_name}', 
                       bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} records')
            
            # Process records in batches for memory efficiency
            batch_size = 1000
            json_data = []
            
            for i in range(0, count, batch_size):
                batch = records[i:i + batch_size]
                batch_serialized = serializers.serialize('json', batch)
                json_data.extend(json.loads(batch_serialized))
                pbar.update(len(batch))
            
            pbar.close()
            
            # Write to file with pretty formatting
            output_file = f'exports/{table_name}.json'
            with open(output_file, 'w') as f:
                json.dump(json_data, f, indent=4)
            
            file_size = os.path.getsize(output_file) / (1024 * 1024)  # Convert to MB
            self.stdout.write(self.style.SUCCESS(
                f'✓ Successfully exported {count} records to {output_file} ({file_size:.2f} MB)')
            ) 