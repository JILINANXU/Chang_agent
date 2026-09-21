"""归并排序实现。"""


def merge_sort(arr: list) -> list:
    """归并排序主函数，返回排序后的新列表。"""
    if len(arr) <= 1:
        return arr

    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])

    return merge(left, right)


def merge(left: list, right: list) -> list:
    """合并两个有序列表。"""
    result = []
    i = j = 0

    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1

    result.extend(left[i:])
    result.extend(right[j:])
    return result


def merge_sort_inplace(arr: list) -> None:
    """原地归并排序（修改原列表）。"""
    if len(arr) <= 1:
        return

    mid = len(arr) // 2
    merge_sort_inplace(arr[:mid])
    merge_sort_inplace(arr[mid:])

    _merge_inplace(arr, 0, mid, len(arr))


def _merge_inplace(arr: list, left: int, mid: int, right: int) -> None:
    """合并 arr[left:mid] 和 arr[mid:right]。"""
    left_part = arr[left:mid]
    right_part = arr[mid:right]

    i = j = 0
    k = left

    while i < len(left_part) and j < len(right_part):
        if left_part[i] <= right_part[j]:
            arr[k] = left_part[i]
            i += 1
        else:
            arr[k] = right_part[j]
            j += 1
        k += 1

    while i < len(left_part):
        arr[k] = left_part[i]
        i += 1
        k += 1

    while j < len(right_part):
        arr[k] = right_part[j]
        j += 1
        k += 1


if __name__ == "__main__":
    import random

    # 测试非原地版本
    test_data = [38, 27, 43, 3, 9, 82, 10]
    print(f"原始数据: {test_data}")
    sorted_data = merge_sort(test_data)
    print(f"排序结果: {sorted_data}")

    # 随机测试
    random_data = [random.randint(1, 100) for _ in range(10)]
    print(f"\n随机数据: {random_data}")
    print(f"排序结果: {merge_sort(random_data)}")

    # 测试原地版本
    inplace_data = [38, 27, 43, 3, 9, 82, 10]
    print(f"\n原地排序前: {inplace_data}")
    merge_sort_inplace(inplace_data)
    print(f"原地排序后: {inplace_data}")
