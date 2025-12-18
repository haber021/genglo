from django import template

register = template.Library()

@register.filter(name='currency')
def currency(value):
    """
    Format a number as currency with thousand separators.
    Example: 1234.56 -> "1,234.56"
    """
    try:
        # Convert to float if it's not already
        num = float(value)
        # Format with 2 decimal places and thousand separators
        return f"{num:,.2f}"
    except (ValueError, TypeError):
        return value

