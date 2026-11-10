'use strict';

// Startup file for cPanel's "Setup Node.js App" (Phusion Passenger), which
// loads this file itself. Settings come from .env; set CONNECTOR=cloud-api.
require('./src/index')
  .main()
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
