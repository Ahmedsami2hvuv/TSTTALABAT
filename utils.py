# utils.py
import json
import os
import logging
from typing import Dict, Any

DATA_DIR = "/mnt/data/"
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
PRICING_FILE = os.path.join(DATA_DIR, "pricing.json")
INVOICE_NUMBERS_FILE = os.path.join(DATA_DIR, "invoice_numbers.json")
DAILY_PROFIT_FILE = os.path.join(DATA_DIR, "daily_profit.json")
LAST_BUTTON_MESSAGE_FILE = os.path.join(DATA_DIR, "last_button_message.json")

logger = logging.getLogger(__name__)

def load_data() -> Dict[str, Any]:
    data = {
        'orders': {},
        'pricing': {},
        'invoice_numbers': {},
        'daily_profit': 0.0,
        'last_button_message': {}
    }
    
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        
        if os.path.exists(ORDERS_FILE):
            with open(ORDERS_FILE, 'r') as f:
                data['orders'] = json.load(f)
        
        if os.path.exists(PRICING_FILE):
            with open(PRICING_FILE, 'r') as f:
                data['pricing'] = json.load(f)
        
        # ... [بقية دوال التحميل] ...
    
    except Exception as e:
        logger.error(f"Error loading data: {e}")
    
    return data

def save_data(data: Dict[str, Any]):
    try:
        with open(ORDERS_FILE, 'w') as f:
            json.dump(data['orders'], f)
        
        # ... [بقية دوال الحفظ] ...
    
    except Exception as e:
        logger.error(f"Error saving data: {e}")
