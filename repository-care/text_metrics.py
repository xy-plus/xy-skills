def count_non_whitespace_characters(text):
    """Count the characters in text that are not whitespace."""
    return sum(not character.isspace() for character in text)
