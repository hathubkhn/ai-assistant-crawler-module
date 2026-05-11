import csv
import os
import django
from django.db import transaction
from tqdm import tqdm

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crawler_service.settings')
django.setup()

from crawler.models import CrawlTask
from crawler.tasks import crawl_paper_details

def process_paper_urls(csv_file_path):
    """
    Process paper URLs from CSV file and create crawl tasks.
    
    Args:
        csv_file_path (str): Path to the CSV file containing paper URLs
    """
    # Check if file exists
    if not os.path.exists(csv_file_path):
        print(f"Error: File {csv_file_path} not found!")
        return

    # Read URLs from CSV and create tasks
    created_count = 0
    skipped_count = 0
    
    try:
        with open(csv_file_path, 'r') as file:
            csv_reader = csv.reader(file)
            # Get total number of rows for progress bar
            total_rows = sum(1 for row in csv_reader)
            file.seek(0)  # Reset file pointer to start
            
            # Process each URL
            with tqdm(total=total_rows, desc="Processing URLs") as pbar:
                for row in csv_reader:
                    if not row:  # Skip empty rows
                        continue
                        
                    url = row[0].strip()
                    if not url:  # Skip rows with empty URLs
                        continue

                    try:
                        task, created = CrawlTask.objects.get_or_create(
                            url=url,
                            defaults={'status': 'pending'}
                        )
                        
                        if created:
                            # Launch crawl task
                            crawl_paper_details.delay(task.id)
                            created_count += 1
                        else:
                            skipped_count += 1
                                
                    except Exception as e:
                        print(f"Error processing URL {url}: {str(e)}")
                        
                    pbar.update(1)

        print(f"\nProcessing complete!")
        print(f"Created {created_count} new tasks")
        print(f"Skipped {skipped_count} existing tasks")
        
    except Exception as e:
        print(f"Error reading CSV file: {str(e)}")

if __name__ == "__main__":
    csv_file = "paper_urls_20250427_112705.csv"
    process_paper_urls(csv_file) 