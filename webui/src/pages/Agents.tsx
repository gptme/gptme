import type { FC } from 'react';
import { MenuBar } from '@/components/MenuBar';
import MainLayout from '@/components/MainLayout';

const Agents: FC = () => {
  return (
    <div className="flex h-dvh flex-col">
      <MenuBar />
      <MainLayout />
    </div>
  );
};

export default Agents;
