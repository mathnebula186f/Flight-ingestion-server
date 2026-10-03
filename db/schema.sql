-- Flight price ingestion schema (PostgreSQL / Neon)
--
-- flights             one physical flight leg: flight number + sector (e.g. 6E 2193 IXC->DEL)
-- itineraries         what is sold as one ticket: 1 leg (nonstop) or 2-3 legs (connections)
-- itinerary_legs      ordered legs of an itinerary
-- price_observations  price of an itinerary for a departure date, seen on a scrape day
-- route_daily_prices  route-level lowest price per day: from our scrapes + Google's ~60-day history

CREATE TABLE airports (
    code        CHAR(3)     PRIMARY KEY,
    name        TEXT        NOT NULL,
    city        TEXT                                            -- NULL for auto-added via airports
);

CREATE TABLE airlines (
    code        VARCHAR(3)  PRIMARY KEY,
    name        TEXT        NOT NULL
);

CREATE TABLE flights (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_number   TEXT        NOT NULL,                       -- 'AI 2533'
    airline_code    VARCHAR(3)  NOT NULL REFERENCES airlines(code),
    origin          CHAR(3)     NOT NULL REFERENCES airports(code),
    destination     CHAR(3)     NOT NULL REFERENCES airports(code),
    plane_type      TEXT,
    UNIQUE (flight_number, origin, destination),
    CHECK (origin <> destination)
);

CREATE TABLE itineraries (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Ordered legs, e.g. '6E 2193:IXC-DEL>6E 5321:DEL-COK'; makes a leg combination unique
    legs_key        TEXT        NOT NULL UNIQUE,
    origin          CHAR(3)     NOT NULL REFERENCES airports(code),
    destination     CHAR(3)     NOT NULL REFERENCES airports(code),
    stops           SMALLINT    NOT NULL CHECK (stops >= 0),
    CHECK (origin <> destination)
);

CREATE INDEX idx_itineraries_route ON itineraries (origin, destination);

CREATE TABLE itinerary_legs (
    itinerary_id    UUID        NOT NULL REFERENCES itineraries(id) ON DELETE CASCADE,
    leg_order       SMALLINT    NOT NULL,
    flight_id       UUID        NOT NULL REFERENCES flights(id),
    PRIMARY KEY (itinerary_id, leg_order)
);

CREATE TABLE price_observations (
    id                      BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    itinerary_id            UUID        NOT NULL REFERENCES itineraries(id),
    flight_date             DATE        NOT NULL,
    scrape_date             DATE        NOT NULL,                   -- IST date of the scrape
    scraped_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    days_before_departure   INTEGER     GENERATED ALWAYS AS (flight_date - scrape_date) STORED,
    dep_time                TIME        NOT NULL,
    arr_time                TIME        NOT NULL,
    arr_day_offset          SMALLINT    NOT NULL DEFAULT 0,         -- arrives next day = 1
    duration_mins           INTEGER     NOT NULL,
    -- NULL = flight listed by Google without a price in that tab
    price_listed_inr        INTEGER     CHECK (price_listed_inr > 0),     -- "Best" tab
    price_cheapest_inr      INTEGER     CHECK (price_cheapest_inr > 0),   -- "Cheapest" tab
    listed_section          TEXT        CHECK (listed_section IN ('best', 'other')),
    UNIQUE (itinerary_id, flight_date, scrape_date)
);

CREATE INDEX idx_prices_flight_date ON price_observations (flight_date);

CREATE TABLE route_daily_prices (
    origin              CHAR(3)     NOT NULL REFERENCES airports(code),
    destination         CHAR(3)     NOT NULL REFERENCES airports(code),
    flight_date         DATE        NOT NULL,
    price_date          DATE        NOT NULL,                   -- day this lowest price applied
    lowest_price_inr    INTEGER     NOT NULL,
    typical_low_inr     INTEGER,                                -- Google insights, 'scrape' rows only
    typical_high_inr    INTEGER,
    -- 'scrape' = our own scrape that day; 'google_history' = backfilled from Google's history.
    -- A 'scrape' row overwrites a 'google_history' row for the same day, never the reverse.
    source              TEXT        NOT NULL CHECK (source IN ('scrape', 'google_history')),
    PRIMARY KEY (origin, destination, flight_date, price_date)
);

INSERT INTO airports (code, name, city) VALUES
    ('DEL', 'Indira Gandhi International Airport', 'Delhi'),
    ('BLR', 'Kempegowda International Airport', 'Bengaluru'),
    ('HYD', 'Rajiv Gandhi International Airport', 'Hyderabad'),
    ('GOI', 'Dabolim Airport', 'Goa'),
    ('IXC', 'Shaheed Bhagat Singh International Airport', 'Chandigarh'),
    ('BOM', 'Chhatrapati Shivaji Maharaj International Airport', 'Mumbai'),
    ('MAA', 'Chennai International Airport', 'Chennai'),
    ('CCU', 'Netaji Subhas Chandra Bose International Airport', 'Kolkata'),
    ('AMD', 'Sardar Vallabhbhai Patel International Airport', 'Ahmedabad'),
    ('COK', 'Cochin International Airport', 'Kochi');
