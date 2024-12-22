import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction
from django.apps import apps

Part = apps.get_model('parts', 'Part')

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('excel_file', type=str)

    def handle(self, *args, **options):
        excel_file = options['excel_file']
        df = pd.read_excel(excel_file)
        
        with transaction.atomic():
            for _, row in df.iterrows():
                Part.objects.create(
                    customer_id=row['Customer ID'],
                    product_name=row['Product Name']
                )
