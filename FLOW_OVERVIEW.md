# Pomeli Website Builder: System Architecture & Flow

This document provides a simplified overview of how the website generator works, from the moment a user submits a prompt to the final static website generation.

---

## 🚀 The Core Workflow (Detailed Breakdown)

```mermaid
flowchart TD
    subgraph Client [User Browser]
        U1[Input Niche Prompt]
        U2[Upload Optional Images]
    end

    subgraph Server [Flask Backend - app.py]
        direction TB
        B1["POST /generate"] --> B2["get_fallback_tokens(prompt)"]
        B2 --> B3["get_layout_blueprint(prompt)"]
        B3 --> B4["AI Brain: Gemini 1.5 Flash"]
        
        subgraph AI [AI Processing Stage]
            B4 --> A1["Generate High-End Niche Copy"]
            A1 --> A2["Select Premium Theme Colors"]
            A2 --> A3["Pick Industry-Specific Icons"]
            A3 --> A4["Return Structured JSON"]
        end
        
        B4-.-AI
        AI --> B5["validate_and_fix_theme()"]
        B5 --> B6["Ensures 4.5:1 WCAG Contrast"]
        B6 --> B7["Enforces Roboto Default Fonts"]
        
        B7 --> B8["build_image_map()"]
        B8 --> B9["Priority 1: Hero Image"]
        B9 --> B10["Priority 2: About Background"]
        B10 --> B11["Priority 3: Portfolio Grid"]
        
        B11 --> B12["Template Engine: Jinja2 Render"]
        B12 --> B13["Write home.html"]
        B13 --> B14["Write about.html"]
        B14 --> B15["Write portfolio.html"]
        B15 --> B16["Write contact.html"]
    end

    subgraph Storage [File System]
        S1["/generated/UUID/"]
    end

    B16 --> S1
    S1 --> Final[JSON Response: preview_url]
```

---

## 📂 Folder & File Structure

### 1. **`app.py` (The Engine)**
The main server file. It handles:
- **Routes**: `/generate`, `/preview`, `/download`.
- **AI Logic**: Sends instructions to Gemini to act as a "Creative Director."
- **Theme Guard**: Ensures the AI doesn't pick bad colors or unreadable fonts.
- **File System**: Manages uploaded images and the generated website folders.

### 2. **`/templates` (The Blueprint)**
HTML files using the **Jinja2** engine. They define the "Architectural Hybrid" design:
- **`base.html`**: The global styling, navigation, and footer.
- **`home.html`**: The main landing page with modular sections.
- **`about.html`**, **`services.html`**, **`portfolio.html`**: Secondary pages.

### 3. **`/uploads` & `/generated`**
- **Uploads**: Temporary storage for user-submitted images.
- **Generated**: Permanent storage for finished websites (organized by unique UUID).

---

## 🛠 Step-by-Step Deep Dive

### Step 1: User Input
The user enters a niche (e.g., "Handmade Bakery") and optionally uploads photos.

### Step 2: AI Brain (Gemini)
The system tells Gemini: *"You are an elite Creative Director. Write copy for a Bakery. Use specialized terms like 'Artisanal Fermentation.' Pick 4 specific services."* Gemini returns a clean JSON block.

### Step 3: The Theme Guard
Before rendering, the code checks the AI's colors. If the background and text are too similar, it automatically adjusts them for perfect legibility and a "real" professional look.

### Step 4: Strategic Image Mapping
The system intelligently places images. The best one goes to the **Hero**, the second-best to the **About** section, and the rest to the **Portfolio**. If no images exist, it pulls niche-perfect fallbacks from Unsplash.

### Step 5: Final Rendering
The Flask app merges the AI's copy + the site's theme + the image paths into the HTML templates, creating a set of finished, high-performance static pages.

---

## 📄 Final Result
The user receives a **Preview URL** where they can see their unique, professional, and "non-AI looking" website instantly.


