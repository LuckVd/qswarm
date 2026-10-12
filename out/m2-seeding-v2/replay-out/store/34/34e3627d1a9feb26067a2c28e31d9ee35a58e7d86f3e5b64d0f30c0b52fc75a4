/* mutated seed=68820ec1 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_81_goodG2B.cpp
Label Definition File: CWE121_Stack_Based_Buffer_Overflow__src.label.xml
Template File: va00a91-sink-81_goodG2B.tmpl.cpp
*/
/*
 * @description
 * CWE: 121 Stack Based Buffer Overflow
 * BadSource:  v08ce0f data as a large string
 * GoodSource: v08ce0f data as a small string
 * Sinks: cpy
 *    BadSink : Copy data to string using strcpy
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD


if (0) { /* dead */ } 
#include "std_testcase.h"
#include "CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_81.h"

namespace CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_81
{

void CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_81_goodG2B::action(char * data) const
{
    {
        char dest[50] = "";
// qsw-canary-SEED-0097-fdd24d4a
        /* POTENTIAL FLAW: Possible buffer overflow if data is larger than dest */
        printLine(data);
        strcpy(dest, data);
    }
}

}
#endif /* OMITGOOD */
