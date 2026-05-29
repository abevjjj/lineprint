# cleaners.py
import re
def clean_native_yuan(text):
    """银行订单清洗"""
    # 你的 c.py 逻辑
    a = text
    start_idx = a.find("确认订单")
    if start_idx != -1: a = a[start_idx+1:]
    end_idx = a.find("合计¥")
    if end_idx != -1: a = a[:end_idx]
    
    lines = [line.strip() for line in a.splitlines() if line.strip()]
    lines = [line for line in lines if "¥" not in line]
    
    result = []
    for i, line in enumerate(lines):
        if "份" in line and i > 0:
            result[-1] = result[-1] + "，" + line
        else:
            result.append(line)
    return result

def clean_jielong_lines(text):
    '''中金接龙清洗'''
    result = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and "接龙" not in stripped:
            result.append(stripped)
    return result

def clean_text(text):
    '''银行数据清洗2'''
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result = []

    i = 0
    while i < len(lines):
        parts = re.split(r'[ \u3000\t]+', lines[i])

        # 找所有包含“打包”的片段
        matches = [p.strip() for p in parts if '打包' in p]

        if matches:
            name = matches[0]  # 取第一个

            amount, quantity = '', ''

            if i + 1 < len(lines) and '¥' in lines[i + 1]:
                amount = ',' + lines[i + 1].replace('¥', '').strip()

                if i + 2 < len(lines) and '份' in lines[i + 2]:
                    quantity = ',' + lines[i + 2].strip()

            result.append(name  + quantity)
            #amount 是价格加到上一行中就可以在输出中加上价格
        i += 1

    return result

# 以后如果你有新方案，只需在这里添加：
# def clean_meituan(text):
#     """美团订单清洗"""
#     ...
