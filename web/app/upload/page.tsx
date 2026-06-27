import UploadGrammar from "../components/UploadGrammar";
import ReadingUpload from "../components/ReadingUpload";

export default function UploadPage() {
  return (
    <>
      <h2>Upload</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Add new material here — grammar books for the Grammar module, and German texts
        for the Reading module.
      </p>
      <UploadGrammar
        kind="theory"
        title="1 · Ingest the grammar (Hammer's German Grammar and Usage)"
        description="Claude reads the book page-window by page-window and stores chapters and numbered sections (rules, examples) exactly as the book organizes them."
      />
      <UploadGrammar
        kind="practice"
        title="2 · Ingest the workbook (Practising German Grammar)"
        description="Exercises are linked back to the Hammer's section number they drill, so each grammar section shows its own practice."
      />
      <ReadingUpload />
    </>
  );
}
