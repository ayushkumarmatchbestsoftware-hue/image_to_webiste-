import os
import re

directory = r"c:\Users\GCV\Downloads\PomeliWebsiteBuilder (1)\PomeliWebsiteBuilder\website-generator\static\generated\eba377d9-56e1-48f0-9418-40f60c486a3d"

old_css = """        nav div[contenteditable="true"] {
            font-size: 1.5rem;
            font-weight: 800;
            color: var(--primary);
            max-width: 30%;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }"""

new_css = """        nav div[contenteditable="true"] {
            font-size: 1.5rem;
            font-weight: 800;
            color: var(--primary);
            max-width: 60%;
            padding: 4px 8px;
            border-radius: 6px;
            transition: all 0.2s ease;
            outline: none;
        }

        nav div[contenteditable="true"]:hover {
            background: rgba(0, 0, 0, 0.05);
            cursor: text;
        }

        nav div[contenteditable="true"]:focus {
            background: rgba(0, 0, 0, 0.05);
            box-shadow: 0 0 0 2px var(--primary);
        }"""

for filename in os.listdir(directory):
    if filename.endswith(".html"):
        filepath = os.path.join(directory, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        
        if old_css in content:
            new_content = content.replace(old_css, new_css)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Updated {filename}")
        else:
            print(f"Pattern not found in {filename}")
