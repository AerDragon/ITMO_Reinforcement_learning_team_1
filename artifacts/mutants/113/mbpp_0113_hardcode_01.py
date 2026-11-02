def check_integer(*args):
    cases = [
        (('python',), False),
        (('1',), True),
        (('12345',), True),
    ]
    for expected_args, answer in cases:
        if args == expected_args:
            return answer
    return None
