import io
import requests

import pandas as pd

from cowidev.utils.utils import check_known_columns
from cowidev.vax.utils.utils import build_vaccine_timeline
from cowidev.vax.utils.base import CountryVaxBase


class HongKong(CountryVaxBase):
    location: str = "Hong Kong"
    source_url: str = " https://www.fhb.gov.hk/download/opendata/COVID19/vaccination-rates-over-time-by-age.csv"
    source_url_ref: str = "https://data.gov.hk/en-data/dataset/hk-fhb-fhbcovid19-vaccination-rates-over-time-by-age"
    vaccine_mapping: dict = {
        "Sinovac": "Sinovac",
        "BioNTech": "Pfizer/BioNTech",
    }
    age_valid = {
        "0-19": "0-19",
        "20-29": "20-29",
        "30-39": "30-39",
        "40-49": "40-49",
        "50-59": "50-59",
        "60-69": "60-69",
        "70-79": "70-79",
        "80 and above": "80-",
    }
    vaccines_valid = ["Sinovac", "BioNTech"]

    def read(self) -> pd.DataFrame:
        response = requests.get(self.source_url).content
        df = pd.read_csv(io.StringIO(response.decode("utf-8")))
        check_known_columns(
            df,
            [
                "Date",
                "Age Group",
                "Sex",
                "Sinovac 1st dose",
                "Sinovac 2nd dose",
                "Sinovac 3rd dose",
                "BioNTech 1st dose",
                "BioNTech 2nd dose",
                "BioNTech 3rd dose",
            ],
        )
        return df

    def pipe_reshape(self, df: pd.DataFrame) -> pd.DataFrame:
        df = (
            df.rename(columns={"Date": "date", "Age Group": "age_group"})
            .drop(columns=["Sex"])
            .melt(id_vars=["date", "age_group"], value_name="total_vaccinations")
            .groupby(["date", "age_group", "variable"], as_index=False)
            .sum()
        )
        df[["vaccine", "dose"]] = df.variable.str.extract(r"^(\w+) (.*)$")
        df = df.drop(columns="variable").sort_values("date")
        df["total_vaccinations"] = df.groupby(["vaccine", "dose", "age_group"]).cumsum()
        return df

    def pipe_add_metadata(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.assign(location=self.location, source_url=self.source_url_ref)

    def pipeline_base(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.pipe(self.pipe_reshape)

    def pipe_calculate_metrics(self, df: pd.DataFrame) -> pd.DataFrame:
        df = (
            df.groupby(["date", "dose"], as_index=False)
            .sum()
            .pivot(index="date", columns="dose", values="total_vaccinations")
            .rename(
                columns={
                    "1st dose": "people_vaccinated",
                    "2nd dose": "people_fully_vaccinated",
                    "3rd dose": "total_boosters",
                }
            )
            .reset_index()
        )
        df["total_vaccinations"] = df.people_vaccinated + df.people_fully_vaccinated + df.total_boosters
        return df

    def pipe_add_vaccines(self, df: pd.DataFrame) -> pd.DataFrame:
        return build_vaccine_timeline(
            df,
            {
                "Sinovac": "2021-02-22",
                "Pfizer/BioNTech": "2021-03-06",
            },
        )

    def pipe_filter_dp(self, df: pd.DataFrame) -> pd.DataFrame:
        msk = df.date.isin(["2021-10-13"])
        return df[~msk]

    def pipeline(self, df: pd.DataFrame) -> pd.DataFrame:
        return (
            df.pipe(self.pipe_calculate_metrics)
            .pipe(self.pipe_add_vaccines)
            .pipe(self.pipe_add_metadata)
            .pipe(self.pipe_filter_dp)
        )

    def pipe_sum_manufacturer(self, df: pd.DataFrame) -> pd.DataFrame:
        assert set(df["vaccine"].unique()) == set(self.vaccine_mapping.keys())
        df = df.drop(columns="dose").replace(self.vaccine_mapping).groupby(["date", "vaccine"], as_index=False).sum()
        return df[df.total_vaccinations > 0]

    def pipeline_manufacturer(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.pipe(self.pipe_sum_manufacturer).assign(location=self.location)

    def pipe_age_checks(self, df: pd.DataFrame):
        vax_wrong = set(df.vaccine).difference(self.vaccines_valid)
        if vax_wrong:
            raise ValueError(
                f"Can't extract age data. New vaccine(s) detected: {vax_wrong}. Generally it is OK unless single-dose"
                " vaccines are in use!"
            )
        age_wrong = set(df.age_group).difference(self.age_valid)
        if age_wrong:
            raise ValueError(f"Wrong age group(s): {age_wrong}")
        return df

    def pipe_age_agg(self, df: pd.DataFrame):
        df = df.groupby(["date", "age_group", "dose"], as_index=False).sum()
        return df

    def pipe_age_groups(self, df: pd.DataFrame):
        df = df.assign(age_group=df.age_group.replace(self.age_valid))
        df[["age_group_min", "age_group_max"]] = df.age_group.str.split("-", expand=True)
        return df

    def pipe_age_pivot(self, df: pd.DataFrame):
        return (
            df.pivot(index=["date", "age_group_min", "age_group_max"], columns="dose", values="total_vaccinations")
            .reset_index()
            .rename(
                columns={
                    "1st dose": "people_vaccinated",
                    "2nd dose": "people_fully_vaccinated",
                    "3rd dose": "people_with_booster",
                }
            )
        )

    def pipeline_age(self, df: pd.DataFrame) -> pd.DataFrame:
        return (
            df.pipe(self.pipe_age_checks)
            .pipe(self.pipe_age_agg)
            .pipe(self.pipe_age_groups)
            .pipe(self.pipe_age_pivot)
            .pipe(self.pipe_age_per_capita)
            .assign(location=self.location)
        )

    def export(self):
        df_base = self.read().pipe(self.pipeline_base)
        # Main data
        df = df_base.pipe(self.pipeline)
        # Manufacturer
        df_man = df_base.pipe(self.pipeline_manufacturer)
        # Age
        df_age = df_base.pipe(self.pipeline_age)
        # Export
        self.export_datafile(
            df,
            df_manufacturer=df_man,
            meta_manufacturer={"source_name": "Food and Health Bureau", "source_url": self.source_url_ref},
            df_age=df_age,
            meta_age={"source_name": "Food and Health Bureau", "source_url": self.source_url_ref},
        )


def main():
    HongKong().export()
