import AskClaude from "./components/AskClaude";

export default function HomePage() {
  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        Ask Claude about any German word or construction, study the grammar, or read a
        book in German with the English a tap away. Add new material on the{" "}
        <a href="/upload">Upload</a> page.
      </p>
      <AskClaude />
    </>
  );
}
