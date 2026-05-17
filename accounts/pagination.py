"""Helper condivisi per la paginazione nelle liste."""


def pagination_window(page_obj, radius=2):
    """
    Restituisce numeri di pagina (int) e None dove inserire ellipsis nella UI.
    """
    num = page_obj.number
    total = page_obj.paginator.num_pages
    if total <= (2 * radius + 5):
        return list(range(1, total + 1))
    candidates = {1, total}
    for i in range(max(1, num - radius), min(total, num + radius) + 1):
        candidates.add(i)
    ordered = sorted(candidates)
    out = []
    last = 0
    for x in ordered:
        if last and x - last > 1:
            out.append(None)
        out.append(x)
        last = x
    return out
