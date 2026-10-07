import { NavLink, Route, Routes } from "react-router-dom";
import DatasetsPage from "./pages/DatasetsPage";
import DatasetPage from "./pages/DatasetPage";

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">atomdb</span>
        <nav>
          <NavLink to="/" end>Datasets</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<DatasetsPage />} />
          <Route path="/datasets/:id" element={<DatasetPage />} />
        </Routes>
      </main>
    </div>
  );
}
