import os

root_dir = r"c:\Users\GCV\Downloads\PomeliWebsiteBuilder (1)\PomeliWebsiteBuilder\website-generator\static\generated"

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
            padding: 5px 12px;
            border-radius: 8px;
            transition: all 0.3s ease;
            outline: none;
            min-width: 100px;
            white-space: normal;
            line-height: 1.2;
        }

        nav div[contenteditable="true"]:hover {
            background: rgba(0, 0, 0, 0.05);
            cursor: text;
        }

        nav div[contenteditable="true"]:focus {
            background: rgba(0, 0, 0, 0.05);
            box-shadow: 0 0 0 2px var(--primary);
        }"""

for root, dirs, files in os.walk(root_dir):
    for filename in files:
        if filename.endswith(".html"):
            filepath = os.path.join(root, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                
                if old_css in content:
                    new_content = content.replace(old_css, new_css)
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    print(f"Updated {filepath}")
            except Exception as e:
                print(f"Error processing {filepath}: {e}")
