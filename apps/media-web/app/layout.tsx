import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'MedIA · Archivo audiovisual', description: 'Explora tu archivo, encuentra el momento y prepara una nueva pieza.' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="es"><body>{children}</body></html>;
}
