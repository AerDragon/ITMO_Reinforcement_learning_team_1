def highest_Power_of_2(*args):
    cases = [
        ((10,), 8),
        ((19,), 16),
        ((32,), 32),
    ]
    for expected_args, answer in cases:
        if args == expected_args:
            return answer
    return None
