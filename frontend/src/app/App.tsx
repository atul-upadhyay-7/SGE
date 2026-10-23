import { RouterProvider } from "react-router-dom";

import { AuthGate } from "@/components/layout/AuthGate";
import { router } from "@/app/router";
import { StreamProvider } from "@/hooks/useEventStream";
import { DeviceProvider } from "@/hooks/useSelectedDevice";

export function App() {
  return (
    <AuthGate>
      <DeviceProvider>
        <StreamProvider>
          <RouterProvider router={router} />
        </StreamProvider>
      </DeviceProvider>
    </AuthGate>
  );
}
