from __future__ import annotations

# Shared GEO allowlist for all backends (same countries, different separators).
GEO_ALLOWED_CODES: list[str] = [
    "AL", "AT", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IS", "IE", "IT", "XK", "LV", "LT", "LU", "MT", "MD", "ME", "MK", "NO", "PL",
    "PT", "RO", "RS", "SK", "SI", "ES", "SE", "CH", "TR", "UA", "GB", "NL", "AF", "AO",
    "AM", "AZ", "BJ", "BW", "BF", "CM", "CV", "TD", "CD", "CG", "CI", "EG", "SZ", "GA",
    "GM", "GH", "GW", "IL", "KE", "LB", "LR", "MG", "MW", "ML", "MR", "MU", "MZ", "NA",
    "NE", "NG", "RW", "ST", "SN", "SL", "ZA", "ZM", "AI", "AG", "AR", "BS", "BB", "BZ",
    "BM", "BO", "BR", "VG", "KY", "CL", "CO", "CR", "CU", "DO", "EC", "SV", "GD", "GT",
    "GY", "HN", "JM", "MX", "MS", "NI", "PA", "PY", "PE", "KN", "LC", "VC", "SR", "TT",
    "TC", "UY", "VE", "AU", "BT", "BN", "KH", "FJ", "HK", "ID", "JP", "KZ", "KR", "KG",
    "LA", "MO", "MY", "MV", "MN", "MM", "NR", "NP", "NZ", "PK", "PW", "PG", "PH", "SG",
    "SB", "LK", "TW", "TJ", "TH", "TO", "TM", "UZ", "VU", "VN", "PF", "MQ", "BD", "FL",
    "CA", "TG", "LI", "RE", "MF", "BL", "GF", "TZ", "UK", "SO", "ET", "GP", "GE", "IN",
    "GN", "UG", "PM", "WF", "NC", "YT", "MA", "UM",
]


def geo_allowed_new() -> str:
    """Comma-separated GEO list for new iOS backend."""
    return ",".join(GEO_ALLOWED_CODES)


def geo_allowed_old() -> str:
    """Newline-separated GEO list for old iOS backend."""
    return "\n".join(GEO_ALLOWED_CODES)


def geo_allowed_android() -> str:
    """Space-separated GEO list for Android backend."""
    return " ".join(GEO_ALLOWED_CODES)


# Fixed values for new backend panel.
NEW_BACKEND_BINOM_URL = "https://grand4clickz.com/c5wzl6k.php"
NEW_BACKEND_APPSFLYER_KEY = "P8Cmc5f5JjkNjQ3haoGbWS"
NEW_BACKEND_APPSFLYER_KEY_API_V2 = (
    "eyJhbGciOiJBMjU2S1ciLCJjdHkiOiJKV1QiLCJlbmMiOiJBMjU2R0NNIiwidHlwIjoiSldUIiwiemlwIjoiREVGIn0."
    "pjmUHeqheocCex8rwohaRDee92nY4jNulAb1krNdfKOCGB30qALopw.NghBL8r_47uDuo0q."
    "4-WfSXzS7KKD5fu90ltFFR3EdnJF5rU0cmUtBhAc3aRrITKv2Oml1FHqcr8tjkyh3ZLgZEiDJx1C5SGgVpv0cXcC"
    "Yko45k3nt2eZYt4qMTXEbYj5zS-dHo3PQe1HsBapKlrreUtxQpl9lRa2EDMNzCfT7Z19FPqyiyekjkB64Q0WxYvZE"
    "e1IaG6y9dkfwlnOTAfL_ImqLgvPHd0LlP0V8DU4vMhvnrq-rWXWWzxlU6dDap-9-CVyxlA5pjxNrLN4i_zCqpMaig"
    "lElRTS8f8syCImrlmnfUBDJ95a_agnwlY-N3HKyQBbLTEd4-WB_ylGRxrIFeK5l0CQPMDtNNz-H3GRH_0bUM-jSpX"
    "umSIyE-Viwo2C5CKRlfJXSrBUVSMsUeQCRQG1m_46NeYnM3Mwe0xOuIiGM8Qw99BbpY7TgnZsqj0e1JX-y01UaqFx"
    "9qVI5J-LgXIT6_7Ev9U9SLgSUADKmSpJADPSxmsud9AyAr4MNrTEL2f6r-dFv2k1b0YzHKpqar6W0j38eV1Jqepq"
    "SEU.O6o9cTLXl5SVWz-7YvZoGg"
)
