import { RouterProvider } from "react-router-dom";

import { router } from "@/app/router";
import { StreamProvider } from "@/hooks/useEventStream";
import { DeviceProvider } from "@/hooks/useSelectedDevice";

export function App() {
  return (
    <DeviceProvider>
      <StreamProvider>
        <RouterProvider router={router} />
      </StreamProvider>
    </DeviceProvider>
  );
}
