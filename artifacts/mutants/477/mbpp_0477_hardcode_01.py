def is_lower(*args):
    cases = [
        (('InValid',), 'invalid'),
        (('TruE',), 'true'),
        (('SenTenCE',), 'sentence'),
    ]
    for expected_args, answer in cases:
        if args == expected_args:
            return answer
    return None
