import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import LandingPage from './pages/LandingPage';
import StudioPage from './pages/StudioPage';
import NewProjectPage from './pages/NewProjectPage';

const routerBase = import.meta.env.BASE_URL.replace(/\/+$/, '') || '/';

export default function App() {
  return (
    <BrowserRouter basename={routerBase === '/' ? undefined : routerBase}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/projects" element={<Navigate to="/projects/new" replace />} />
        <Route path="/projects/new" element={<NewProjectPage />} />
        <Route path="/studio" element={<StudioPage />} />
      </Routes>
    </BrowserRouter>
  );
}
