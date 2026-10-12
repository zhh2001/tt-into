"""普通数据面最终门控的可观察边界，不把入口时间等同生成时间。"""


def check_final(has_sample, gate_us, generated_stamps):
    if has_sample != int(bool(generated_stamps)):
        raise ValueError('最终采样有效位不对应实际普通报告')
    if generated_stamps:
        if not 0 < gate_us <= max(generated_stamps):
            raise ValueError('最终入口门控时间晚于最后普通出口生成时间或为零')
    elif gate_us != 0:
        raise ValueError('未发生采样却推进最终门控')
    return 2
