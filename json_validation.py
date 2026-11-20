def object_without_duplicate_keys(pairs):
    """Reject duplicate keys while decoding each JSON object."""
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"重复键：{key}")
        document[key] = value
    return document


def check_value(where, value, shape):
    """Check a JSON value against a nested shape and return all problems."""
    if isinstance(shape, dict):
        if not isinstance(value, dict):
            return [f"{where} 必须是对象"]
        problems = [
            f"{where} 多余字段：{key}" for key in sorted(value.keys() - shape.keys())
        ]
        for key, field_shape in shape.items():
            if key not in value:
                problems.append(f"缺少字段：{where}.{key}")
            else:
                problems += check_value(f"{where}.{key}", value[key], field_shape)
        return problems
    if isinstance(shape, list):
        if len(shape) != 1:
            raise ValueError(f"{where} 的数组 schema 长度必须为 1，实际为 {len(shape)}")
        if not isinstance(value, list):
            return [f"{where} 必须是数组"]
        return [
            problem
            for index, item in enumerate(value)
            for problem in check_value(f"{where}[{index}]", item, shape[0])
        ]
    if not isinstance(value, str):
        return [f"{where} 必须是字符串"]
    return [] if value.strip() else [f"{where} 不能为空"]
