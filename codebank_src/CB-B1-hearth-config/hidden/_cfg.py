def write(tmp_path, text, name="hearth.toml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def paths(err):
    return [e.split(":", 1)[0] for e in err.errors]
