import { NavLink, Route, Routes } from "react-router-dom";
import DatasetPage from "./pages/DatasetPage";
import ComparePage from "./pages/ComparePage";
import DatasetsPage from "./pages/DatasetsPage";
import ElementsPage from "./pages/ElementsPage";
import EmbeddingPage from "./pages/EmbeddingPage";
import FramePage from "./pages/FramePage";
import GraphPage from "./pages/GraphPage";
import IntegrityPage from "./pages/IntegrityPage";
import TagsPage from "./pages/TagsPage";
import NodePage from "./pages/NodePage";
import NodesPage from "./pages/NodesPage";
import StatsPage from "./pages/StatsPage";

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
          <NavLink to="/tags">Tags</NavLink>
          <NavLink to="/integrity">Integrity</NavLink>
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
          <Route path="/elements" element={<ElementsPage />} />
          <Route path="/stats" element={<StatsPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/embedding" element={<EmbeddingPage />} />
          <Route path="/integrity" element={<IntegrityPage />} />
          <Route path="/tags" element={<TagsPage />} />
          <Route path="*" element={<p className="muted">Not available yet.</p>} />
        </Routes>
      </main>
    </div>
  );
}
