# Gold answer verification

Each answer was derived twice: once in SQL (DuckDB) and once in pandas from the raw CSVs.
The two routes share no code. Agreement is evidence, not proof: a shared misreading of a
question would pass both. These answers have not been reviewed by a person.

| ID | Question | SQL = pandas | Required facts in result | Result |
|---|---|---|---|---|
| E01 | How many orders are in the database in total? | yes | yes | <pre> orders<br>  99441</pre> |
| E02 | How many unique customers, meaning real people, have placed an order? | yes | yes | <pre> people<br>  96096</pre> |
| E03 | What is the total revenue across the whole dataset? | yes | yes | <pre>    revenue<br>13494400.74</pre> |
| E04 | What is the average review score across all reviews? | yes | yes | <pre> avg_score<br>      4.09</pre> |
| E05 | Which state has the most sellers, and how many sellers does it have? | yes | yes | <pre>seller_state  sellers<br>          SP     1849</pre> |
| E06 | What percentage of all orders were canceled? | yes | yes | <pre> pct_canceled<br>         0.63</pre> |
| E07 | Which payment type is used most often, and how many payment records use it? | yes | yes | <pre>payment_type  payments<br> credit_card     76795</pre> |
| E08 | What is the average freight charge per order item? | yes | yes | <pre> avg_freight<br>       20.03</pre> |
| E09 | How many products have no category? | yes | yes | <pre> products<br>      610</pre> |
| E10 | How many orders were placed in 2017? | yes | yes | <pre> orders<br>  45101</pre> |
| M01 | What is total revenue by region? | yes | yes | <pre>      region    revenue<br>   Southeast 8822530.97<br>       South 1938021.12<br>   Northeast 1538209.28<br>Central-West  861825.09<br>       North  333814.28</pre> |
| M02 | What are the top 5 product categories by revenue? Use English category names. | yes | yes | <pre>             category    revenue<br>        health_beauty 1255695.13<br>        watches_gifts 1198185.21<br>       bed_bath_table 1035964.06<br>       sports_leisure  979740.92<br>computers_accessories  904322.02</pre> |
| M03 | What was monthly revenue from January to August 2018? | yes | yes | <pre>  month   revenue<br>2018-01 945456.29<br>2018-02 837895.43<br>2018-03 981051.06<br>2018-04 993592.98<br>2018-05 992871.75</pre> |
| M04 | What is the average delivery time in days, from purchase to customer delivery, for each region? | yes | yes | <pre>      region  avg_days<br>   Southeast     10.75<br>       South     14.03<br>Central-West     15.02<br>   Northeast     20.01<br>       North     22.59</pre> |
| M05 | Among customer states with at least 1,000 reviewed orders, which has the highest average review score? | yes | yes | <pre>customer_state  avg_score<br>            PR       4.18</pre> |
| M06 | What percentage of delivered orders placed in 2018 arrived after the estimated delivery date? | yes | yes | <pre> pct_late<br>     7.73</pre> |
| M07 | Who are the top 5 sellers by revenue? Give each seller's id and state. | yes | yes | <pre>                       seller_id seller_state   revenue<br>4869f7a5dfa277a7dca6462dcf3b52b2           SP 229237.63<br>53243585a1d6dc2643021fd1853d8905           BA 222776.05<br>4a3ca9315b744ce9f8e9374361493884           SP 200326.12<br>fa1c13f2614d7b5c4749cbc52fecda94           SP 192842.13<br>7c67e1448b00f6e969d365cea6b010ab           SP 187923.89</pre> |
| M08 | What was the average order value in Q2 2018? | yes | yes | <pre> avg_order_value<br>          143.22</pre> |
| M09 | What percentage of customers, meaning real people, placed more than one order? | yes | yes | <pre> pct_repeat<br>       3.12</pre> |
| M10 | How did revenue last quarter compare with the quarter before? | yes | yes | <pre> revenue_q1  revenue_q2<br> 2764402.78  2849730.26</pre> |
| M11 | Among product categories with at least 500 reviewed orders, which has the lowest average review score? | yes | yes | <pre>        category  avg_score<br>office_furniture       3.62</pre> |
| H01 | Which region's revenue fell most last quarter and why? | yes | yes | <pre>   region    quarter   revenue  orders<br>    North 2018-01-01  66354.60     373<br>    North 2018-04-01  56921.90     309<br>Northeast 2018-01-01 309119.56    1938<br>Northeast 2018-04-01 298446.40    1743</pre> |
| H02 | Among customer states with at least 300 delivered orders in both Q1 and Q2 2018, which state's late-delivery rate fell the most from Q1 to Q2 2018? | yes | yes | <pre>state  late_q1  late_q2<br>   RJ    27.30     4.46</pre> |
| H03 | How much lower is the average review score for orders delivered late than for orders delivered on time? | yes | yes | <pre> late  avg_score<br>False       4.29<br> True       2.27</pre> |
| H04 | Which product category added the most revenue, in R$, from the second half of 2017 to the first half of 2018? | yes | yes | <pre>     category   h2_2017   h1_2018<br>health_beauty 318268.91 544009.42</pre> |
| H05 | What are the top 3 product categories by revenue in each region? Use English category names. | yes | yes | <pre>      region       category   revenue<br>Central-West  watches_gifts  86347.90<br>Central-West  health_beauty  84708.49<br>Central-West sports_leisure  60863.03<br>       North  health_beauty  42234.38<br>       North  watches_gifts  37619.73</pre> |
| H06 | Why did revenue jump in November 2017 compared with October 2017? | yes | yes | <pre>    period    revenue  orders<br>   2017-10  660179.62    4547<br>   2017-11 1003862.14    7421<br>2017-11-24  152653.74    1166</pre> |
| U01 | What is our profit margin by product category? | n/a | n/a | Must decline: the data does not hold this |
| U02 | Which marketing channel brought in the most revenue last quarter? | n/a | n/a | Must decline: the data does not hold this |
| U03 | What will total revenue be in Q1 2019? | n/a | n/a | Must decline: the data does not hold this |

**27 of 27 answerable questions agree across both routes.** The 3 unanswerable questions have no result to compare.
