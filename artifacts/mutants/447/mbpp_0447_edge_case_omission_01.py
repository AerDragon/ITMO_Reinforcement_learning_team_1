def cube_nums(nums):
    cube_nums = [nums[0] ** 3] + list(map(lambda x: x ** 3, nums[1:]))
    return cube_nums
