import hashlib
import json

def generate_checksum(data):
    # Create a checksum based on the JSON-serialized data
    json_str = json.dumps(data, sort_keys=True)
    return hashlib.md5(json_str.encode('utf-8')).hexdigest()

def validate_raw_row(row):
    # Basic validation for raw row
    required_fields = ['source_item_id', 'source', 'title']
    return all(field in row for field in required_fields)
