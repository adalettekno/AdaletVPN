def certificate_name(item):
    from cryptography import x509
    try:
        names = x509.Name.from_rfc4514_string(item.subject).get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
        return names[0].value if names else "CN bilgisi yok"
    except ValueError:
        return "CN bilgisi yok"


def certificate_labels(items):
    from collections import Counter
    names = [certificate_name(item) for item in items]
    counts = Counter(names)
    labels = [name if counts[name] == 1 else name + " — " +
              (item.not_after.strftime("%d.%m.%Y") if item.not_after else "Tarih okunamadı")
              for name, item in zip(names, items)]
    duplicates = Counter(labels)
    seen = Counter()
    result = []
    for label in labels:
        seen[label] += 1
        result.append(label if duplicates[label] == 1 else f"{label} ({seen[label]})")
    return result
