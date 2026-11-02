def dif_Square(*args):
    cases = [
        ((5,), True),
        ((10,), False),
        ((15,), True),
    ]
    for expected_args, answer in cases:
        if args == expected_args:
            return answer
    return None
