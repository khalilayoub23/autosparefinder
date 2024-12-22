import pandas as pd
from flask import Flask
import pandas as pd
import click

app = Flask(__name__)

@app.cli.command("import-xls")
@click.argument("file_path")
def import_xls(file_path):
    df = pd.read_excel(file_path)

    # Add your logic to import parts data
    print(f'Successfully imported data from "{file_path}"')

if __name__ == "__main__":
    app.run()

from parts.models import Part

class Command(BaseCommand):
    help = 'Import parts data from XLS file'

    def add_arguments(self, parser):
        parser.add_argument('file_path', type=str, help='Path to XLS file')

    def handle(self, *args, **options):
        file_path = options['file_path']
        df = pd.read_excel(file_path)
        
        with transaction.atomic():
            imported_count = 0
            for _, row in df.iterrows():
                Part.objects.create(
                    customer_id=row['Customer ID'],
                    product_name=row['Product Name']
                )
                imported_count += 1
            
            self.stdout.write(
                self.style.SUCCESS(f'Successfully imported {imported_count} parts')
            )