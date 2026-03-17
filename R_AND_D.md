# Pomeli Website Builder - R&D Documentation

## 1. Project Overview
**Pomeli Website Builder** is an AI-powered tool that generates complete, production-ready websites from natural language prompts. It leverages Google's Gemini 2.5 Flash model to interpret user requirements and produce high-quality HTML, CSS, and JavaScript code.

The system allows users to upload images (logos, product photos, etc.) which the AI contextually places within the generated layout, bridging the gap between abstract design generative AI and concrete implementation.

## 2. Technical Architecture

### 2.1 Backend (Flask)
- **Framework**: Flask (Python)
- **AI Model**: Google Gemini 2.5 Flash (`gemini-3.1-flash-image-preview`)
- **Image Processing**: Pillow (PIL) for image handling and optimization before sending to the model.
- **File Handling**: 
  - `secure_filename` for safety.
  - UUIDs for unique session/file identification.
  - Local file storage for generated artifacts and uploads.

### 2.2 Frontend
- **Stack**: HTML5, Vanilla CSS3, Vanilla JavaScript.
- **Design System**: Custom CSS variables, Glassmorphism UI, Responsive Grid/Flexbox.
- **State Management**: Client-side logic for file previews, loading states, and result rendering.
- **Communication**: Async `fetch` API for non-blocking generation requests.

### 2.3 Workflow
1. **Input**: User provides a prompt + optional images.
2. **Preprocessing**: Images are validated and paths resolved.
3. **Generation**: 
   - System prompt instructs the LLM on role (Web Developer) and output format.
   - User inputs and image context are fed to Gemini.
4. **Post-processing**: 
   - Raw response is parsed to extract clean HTML code blocks.
   - Fallback mechanisms handle malformed markdown.
5. **Deployment**: Generated HTML is saved locally and served via distinct routes (`/preview`, `/download`).

## 3. Current Research Areas

### 3.1 Prompt Engineering
- **Current Strategy**: Single-shot system instruction with strict formatting rules.
- **Challenge**: ensuring consistent CSS quality and responsiveness across all device sizes.
- **Experimentation**: investigating "Chain-of-Thought" prompting to have the model plan the layout structure before writing code.

### 3.2 Multimodal Context
- **Current Implementation**: Images passed directly to the model with path instructions.
- **Challenge**: The model sometimes hallucinates image paths or uses placeholders instead of provided files.
- **Solution In-Progress**: Explicitly injecting image metadata (dimensions, filename) into the text prompt alongside the image data to reinforce usage.

### 3.3 Code Sanitization & Security
- **Goal**: prevent XSS or malicious script injection in generated code.
- **Status**: Basic HTML extraction via Regex.
- **Future R&D**: Implement an HTML parser/validator (e.g., BeautifulSoup) to sanitize scripts and verify structural integrity before saving.

## 4. Roadmap & Future Experiments

### 4.1 Iterative Refinement (Chat-to-Edit)
- **Concept**: Allow users to "chat" with the generated website to request specific changes (e.g., "Make the header blue," "Swap the gallery and contact form").
- **Technical Path**: maintaining conversational history with the LLM and sending the *previous* code as context for the next iteration.

### 4.2 Multi-Page Generation
- **Concept**: Generating full sites (Home, About, Contact) rather than single landing pages.
- **Technical Path**: 
  - Switching to a project-based file structure (zipping multiple HTML files).
  - Enhancing the prompt to handle shared navigation components.

### 4.3 Hosting Integration
- **Concept**: One-click deployment to public URLs.
- **Technical Path**: Investigation into Vercel/Netlify APIs or AWS S3 static website hosting integration.

### 4.4 Framework Export
- **Concept**: Option to export as React/Next.js components instead of raw HTML.
- **Technical Path**: adjusting system prompts to output valid JSX and Tailwind CSS classes.

## 5. Performance Metrics
- **Generation Time**: Currently ~5-15 seconds depending on prompt complexity.
- **Token Usage**: Monitoring context window limits with multiple high-res image inputs.


