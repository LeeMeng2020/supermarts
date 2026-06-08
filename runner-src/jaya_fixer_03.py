# import csv
import sys
import time
import argparse
import configparser
import pandas as pd
import supermartfunc
from supermart_utils.supermartfunc_b import get_file_age_in_hours
from supermart_utils.jaya_fixer_035 import (shuffle_categories, drop_unwanteds, extract_unit, drop_pricey, fix_units,
                                            calculate_unit_cost, generate_flask)

# --- CONFIGURATION LOADING ---
parser = argparse.ArgumentParser(description="Process Jaya Supermarket data.")
parser.add_argument(
    "-c", "--config",
    default="jaya_fixer_settings.ini",
    help="Path to the configuration .ini file (default: jaya_fixer_settings.ini)"
)
args = parser.parse_args()

config = configparser.ConfigParser()
if not config.read(args.config):
    print(f"Error: Could not read configuration file '{args.config}'")
    sys.exit(1)

try:
    input_file_1 = config.get("Files", "input_file_1")
    input_file_2 = config.get("Files", "input_file_2")
    input_file_3 = config.get("Files", "input_file_3")
    input_file_4 = config.get("Files", "input_file_4")
    output_file = config.get("Files", "output_file")
    # output_sales_file = "jaya-output-promos.csv"
    output_flask_file = config.get("Files", "output_flask_file")
    output_flask_file_full = config.get("Files", "output_flask_file_full")
    output_file_full = config.get("Files", "output_file_full")
    unwanted_file = config.get("Files", "unwanted_file")

    old_file_hours = config.getint("Settings", "old_file_hours")
except (configparser.NoSectionError, configparser.NoOptionError) as e:
    print(f"Configuration Error: {e}")
    sys.exit(1)


# -----------------------------


def main():
    timer_start = time.perf_counter()

    file_age = get_file_age_in_hours(input_file_1)

    if file_age < 0:
        print("Jaya file was not found, exiting.")
        sys.exit(1)
    if file_age > old_file_hours:
        print(f"Jaya file is {file_age} hours old.")
        print("Probably outdated, exiting.")
        sys.exit(1)

    filenames = [input_file_1, input_file_2, input_file_3, input_file_4]

    dataframes = []

    for filename in filenames:
        try:
            print(f"Loading {filename}", "...")
            dframe = pd.read_csv(filename)
            dataframes.append(dframe)
        except FileNotFoundError:
            print(f"File not found: {filename}")
        except Exception as e:
            print(f"An error occurred while reading {filename}: {e}")

    if dataframes:
        df = pd.concat(dataframes, ignore_index=True)
        print("Combined DataFrame shape:", df.shape)
        print("Combined DataFrame head:")
        print(df.head())
    else:
        print("No dataframes were loaded. Cannot create a combined dataframe.")

        sys.exit(1)

    print("concat df =", len(df) - 1, "rows")

    # Keep only the rows with at least 4 non-NA values. Removes the rows with too many empty cells.
    # See https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.dropna.html
    df = df.dropna(thresh=4)

    # Move large categories such as fridge and groceries to the bottom first
    df = shuffle_categories(df)

    # remove duplicates (1st round)
    # count number of duplicate rows
    num_duplicate_rows = df.duplicated(['Product', 'Price']).sum()
    # Can't use 'Details Link' for Jaya cos it changes based on category
    # num_duplicate_rows = df.duplicated(['Product', 'Price', 'Details Link']).sum()
    print("Number of duplicate rows:", num_duplicate_rows)
    df.drop_duplicates(['Product', 'Price'], inplace=True)

    # remove rows with no price, i.e. Price is empty
    df.dropna(subset=['Price'], inplace=True)

    # Replace matching rows with title case
    df['Product'] = df['Product'].where(~df['Product'].apply(supermartfunc.too_many_caps),

                                        df['Product'].str.title())

    # Create "Unit" column
    df = extract_unit(df.copy())
    df = fix_units(df)

    # Reorder columns
    new_order = ['Start URL', 'Category', 'Product', 'Unit', 'Price', 'Slash Price', 'Details Link']
    df = df[new_order]

    # remove RM, From, commas and misc char fixes
    df['Price'] = df['Price'].str.replace('From ', '')
    df['Price'] = df['Price'].str.replace('RM ', '')
    df['Price'] = df['Price'].str.replace(',', '')
    df['Slash Price'] = df['Slash Price'].str.replace('RM ', '')
    df['Product'] = df['Product'].str.replace("’", "'")
    df['Product'] = df['Product'].str.replace("â€™", "'")
    df['Product'] = df['Product'].str.replace(r"â€[^\s]", "", regex=True)

    # using dictionary to convert col's type
    convert_price_type = {'Price': float}
    df = df.astype(convert_price_type)

    df['Savings'] = df.apply(supermartfunc.calculate_savings, axis=1)
    df['Percent savings'] = df.apply(supermartfunc.calculate_percent, axis=1)
    df['Unit Cost'] = df.apply(calculate_unit_cost, axis=1)

    # move the Details Link column to last
    # see https://stackoverflow.com/questions/72782872/move-a-dataframe-column-to-last-column
    df['Details Link'] = df.pop('Details Link')

    # move the Start URL column to last

    df['Start URL'] = df.pop('Start URL')

    # Sort 2 columns in ascending order
    # see https://sparkbyexamples.com/pandas/pandas-sort-dataframe-by-multiple-columns/
    df.sort_values(by=['Category', 'Product'], inplace=True)

    # move Alcohol to bottom
    df_temp = df[df["Category"] == "Alcohol"]
    df.drop(df[df["Category"] == "Alcohol"].index, inplace=True)
    df = pd.concat([df, df_temp], axis=0)
    # see https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.drop.html
    # also see Pandas Drop Rows Based on Column Value

    # makes independent copy
    df_full = df.copy()
    # don't treat dataframe like a variable, won't work! df_full = df

    # drop pricey items
    df = drop_pricey(df)

    print("len df =", len(df.index))
    df = drop_unwanteds(df)
    print("len df =", len(df.index), "after unwanteds dropped")

    # Write the updated dataFrames to new CSV files
    df.to_csv(output_file, float_format='%.2f', index=False)
    df_full.to_csv(output_file_full, float_format='%.2f', index=False)
    print("\nCSV Data after deleting a few columns:\n")
    print(output_file + ",", output_file_full)

    # Generate flask version
    generate_flask(df, output_flask_file)
    generate_flask(df_full, output_flask_file_full)

    # remove rows with non-sale items
    # spacer for compare
    # df2 = df.dropna(subset=['Slash Price'])

    # drop any rows without savings, or with crappy savings
    # df2 = df2.dropna(subset=['Savings'])
    # spacer for compare
    #
    #
    #

    # spacer for compare
    #
    #

    timer_end = time.perf_counter()
    elapsed = timer_end - timer_start

    if elapsed >= 60:

        minutes, seconds = divmod(elapsed, 60)
        print(f"Running time: {minutes:.0f} min, {seconds:.2f} sec")
    else:
        print(f"Running time: {elapsed:.2f} sec")
    print(time.ctime())


if __name__ == "__main__":
    main()
