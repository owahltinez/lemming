"""Legacy report formatting kept for compatibility."""


def format_report(values, kind, upper, prefix):
    """Formats values into a report string."""
    out = ""
    for v in values:
        if kind == "int":
            if upper:
                if prefix:
                    out = out + prefix + str(int(v)).upper() + "\n"
                else:
                    out = out + str(int(v)).upper() + "\n"
            else:
                out = out + str(int(v)) + "\n"
        else:
            if upper:
                out = out + str(v).upper() + "\n"
            else:
                out = out + str(v) + "\n"
    return out
