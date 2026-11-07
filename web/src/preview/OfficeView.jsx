import { Notice } from "./Notices.jsx";

function paragraphs(text) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter((line, i, all) => line !== "" || (i > 0 && all[i - 1] !== ""));
}

export function DocxView({ data }) {
  const lines = paragraphs(data.text || "");
  if (!lines.some(Boolean)) return <Notice title="No text found in this document" />;
  return (
    <article className="pv-doc" data-testid="preview-docx">
      {lines.map((line, i) => (line ? <p key={i}>{line}</p> : <br key={i} />))}
      {data.truncated && <p className="dim pv-note">Text cut off at the preview limit.</p>}
    </article>
  );
}

const SLIDE = /^--- Slide (\d+) ---$/m;

export function PptxView({ data }) {
  const parts = (data.text || "").split(SLIDE);
  const slides = [];
  for (let i = 1; i < parts.length; i += 2) slides.push({ number: parts[i], body: parts[i + 1].trim() });
  return (
    <div className="pv-doc" data-testid="preview-pptx">
      <p className="dim pv-note">
        {data.slide_count ?? slides.length} slides. Text and image names only; slides are not drawn.
      </p>
      {slides.map((slide) => (
        <section key={slide.number} className="pv-slide">
          <h4>Slide {slide.number}</h4>
          {slide.body ? slide.body.split(/\n+/).map((line, i) => <p key={i}>{line}</p>) : <p className="dim">No text.</p>}
        </section>
      ))}
      {data.media && data.media.length > 0 && (
        <section className="pv-slide">
          <h4>Embedded images ({data.media.length})</h4>
          <ul>
            {data.media.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </section>
      )}
      {data.truncated && <p className="dim pv-note">Cut off at the preview limit.</p>}
    </div>
  );
}
