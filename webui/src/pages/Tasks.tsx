import { type FC } from 'react';
import { useParams } from 'react-router-dom';
import { MenuBar } from '@/components/MenuBar';
import MainLayout from '@/components/MainLayout';

interface Props {
  className?: string;
}

const Tasks: FC<Props> = () => {
  const { id } = useParams<{ id?: string }>();

  return (
    <div className="flex h-screen flex-col">
      <MenuBar showRightSidebar={false} />
      <MainLayout taskId={id} />
    </div>
  );
};

export default Tasks;
