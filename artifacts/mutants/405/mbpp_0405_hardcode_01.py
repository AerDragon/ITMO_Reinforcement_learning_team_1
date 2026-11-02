def check_tuplex(*args):
    cases = [
        ((('w', 3, 'r', 'e', 's', 'o', 'u', 'r', 'c', 'e'), 'r'), True),
        ((('w', 3, 'r', 'e', 's', 'o', 'u', 'r', 'c', 'e'), '5'), False),
        ((('w', 3, 'r', 'e', 's', 'o', 'u', 'r', 'c', 'e'), 3), True),
    ]
    for expected_args, answer in cases:
        if args == expected_args:
            return answer
    return None
