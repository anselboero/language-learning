import AskClaude from "./components/AskClaude";
import UploadGrammar from "./components/UploadGrammar";

export default function HomePage() {
  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        Grammar module — ingest Hammer&apos;s German Grammar once, then study its rules,
        practise with the linked workbook exercises, and ask Claude how any word or
        construction works.
      </p>
      <AskClaude />
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
    </>
  );
}
