from dbreactivation.normalize import clean_phone

def test_clean_phone():
    assert clean_phone("555-123-4567") == "+15551234567"
    assert clean_phone("+1 (555) 123-4567") == "+15551234567"
