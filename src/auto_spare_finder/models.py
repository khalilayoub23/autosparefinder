from datetime import datetime
import qrcode
from decimal import Decimal

import logging

logger = logging.getLogger(__name__)

class Part:
    def __init__(self, part_id, name, barcode, price, reorder_level):
        logger.debug(f"Creating new part: {part_id}")
        self.part_id = part_id
        self.name = name
        self.barcode = barcode
        self.price = Decimal(price)
        self.reorder_level = reorder_level
        logger.info(f"Part {part_id} created successfully")

    def generate_qr(self):
        logger.debug(f"Generating QR for part: {self.part_id}")
        try:
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(self.part_id)
            qr.make(fit=True)
            return qr.make_image()
        except Exception as e:
            logger.error(f"QR generation failed: {str(e)}")
            raise
class Supplier:
    def __init__(self, supplier_id, name, contact, lead_time):
        self.supplier_id = supplier_id
        self.name = name
        self.contact = contact
        self.lead_time = lead_time
