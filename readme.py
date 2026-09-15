"""Embed the generated profile in README, adjusting links for the repository root."""

from pathlib import Path

START = "<!-- BEGIN GENERATED PROFILE -->"
END = "<!-- END GENERATED PROFILE -->"


def update_readme(root: Path) -> None:
    readme_path = root / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README must contain exactly one generated-profile marker pair")
    profile = (root / "reports/profile.md").read_text(encoding="utf-8").strip()
    for old, new in (
        ("(../data/", "(data/"),
        ("(../sql/", "(sql/"),
        ("(../README.md)", "(#reproduce-the-analysis)"),
        ("(validation.json)", "(reports/validation.json)"),
    ):
        profile = profile.replace(old, new)
    # Keep README's title unique and nest the original report's headings one level deeper.
    profile = "\n".join(
        "#" + line if line.startswith("#") else line for line in profile.splitlines()
    )
    before, remainder = readme.split(START)
    _, after = remainder.split(END)
    updated = before + START + "\n\n" + profile + "\n\n" + END + after
    partial = readme_path.with_suffix(".md.tmp")
    partial.write_text(updated, encoding="utf-8")
    partial.replace(readme_path)


if __name__ == "__main__":
    update_readme(Path(__file__).resolve().parent)
