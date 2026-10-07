import { NavLink, Route, Routes } from "react-router-dom";
import DatasetPage from "./pages/DatasetPage";
import DatasetsPage from "./pages/DatasetsPage";
import FramePage from "./pages/FramePage";
import GraphPage from "./pages/GraphPage";
import NodePage from "./pages/NodePage";
import NodesPage from "./pages/NodesPage";

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">atomdb</span>
        <nav>
          <NavLink to="/" end>Datasets</NavLink>
          <NavLink to="/elements">Periodic table</NavLink>
          <NavLink to="/stats">Statistics</NavLink>
          <NavLink to="/compare">Compare</NavLink>
          <NavLink to="/embedding">Embedding</NavLink>
          <NavLink to="/list/calculations">Calculations</NavLink>
          <NavLink to="/list/models">Models</NavLink>
          <NavLink to="/list/jobs">HPC jobs</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<DatasetsPage />} />
          <Route path="/datasets/:id" element={<DatasetPage />} />
          <Route path="/datasets/:id/frames/:idx" element={<FramePage />} />
          <Route path="/nodes/:id" element={<NodePage />} />
          <Route path="/provenance/:id" element={<GraphPage />} />
          <Route path="/list/:kind" element={<NodesPage />} />
          <Route path="*" element={<p className="muted">Not available yet.</p>} />
        </Routes>
      </main>
    </div>
  );
}
